import os
import re
import json
import base64
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlencode

import aiohttp
from Crypto import Random
from Crypto.Cipher import AES

from .models import AppSettings
from .helpers.parsers import filename_parse

logger = logging.getLogger(__name__)


class BroadcastService:
    """방송 콘텐츠 생성 서비스 (메타데이터 조회 + 암호화)"""

    def __init__(
        self, session: aiohttp.ClientSession, settings: AppSettings
    ) -> None:
        self.session = session
        self.settings = settings

    def get_gds_content(
        self, path: str, mode: str, file_count: int = 0, total_size: int = 0
    ) -> str:
        data = {
            "t1": "gds_tool",
            "t2": "fp",
            "t3": "user",
            "data": {
                "gds_path": path,
                "scan_mode": mode,
                "count": file_count,
                "size": total_size,
            },
        }
        encrypted_data = self.encrypt(
            json.dumps(data), self.settings.broadcast.encrypt.key
        )
        return f"```^{encrypted_data}```"

    async def get_downloader_content(
        self, path: str, item: str, file_count: int = 0, total_size: int = 0
    ) -> str:
        logger.debug(f"{path=} {item=} {file_count=} {total_size=}")
        full_path = Path(path)
        category, module = self._get_category_and_module(full_path)
        parsed_parts = filename_parse(full_path.name)
        logger.debug(f"{parsed_parts=}")
        file_title = parsed_parts.get("title") or full_path.stem
        year = parsed_parts.get("year") or 1900
        metadata = await self._fetch_metadata(full_path, category, file_title, year)
        if category == "movie":
            builder = self._build_movie_data
        else:
            builder = self._build_vod_data
        data = builder(
            metadata=metadata,
            path=full_path,
            item=item,
            module=module,
            file_title=file_title,
            file_count=file_count,
            total_size=total_size,
            parsed=parsed_parts,
        )
        encrypted_data = self.encrypt(
            json.dumps(data), self.settings.broadcast.encrypt.key
        )
        return f"```^{encrypted_data}```"

    def _get_category_and_module(self, full_path: Path) -> tuple[str, str]:
        for mod_rule in self.settings.broadcast.module_rules:
            if mod_rule.is_match(str(full_path)) or mod_rule.is_relative(full_path):
                logger.debug(
                    f"Matched modules: metadata='{mod_rule.metadata}' bot_downloader='{mod_rule.bot_downloader}' path='{str(full_path)}'"
                )
                return mod_rule.metadata, mod_rule.bot_downloader
        return "ktv", "vod"

    async def _fetch_metadata(
        self, path: Path, category: str, file_title: str, year: int
    ) -> dict[str, Any]:
        logger.debug(f"{category=} {file_title=}")
        path_str = str(path)
        if tmdb_id := self.settings.tmdb.get_tmdb_id(path_str):
            code_prefix = "MT" if category == "movie" else "FT"
            return await self._lookup_metadata(f"{code_prefix}{tmdb_id}")
        else:
            search_keywords = self.settings.broadcast.get_search_keywords(file_title)
            search_categories = sorted(
                ["ftv", "ktv", "movie"], key=lambda x: x != category
            )
            search_targets = (
                (cat, kw) for cat in search_categories for kw in search_keywords
            )
            search_result = None
            for cat, kw in search_targets:
                if search_result := await self._search_metadata(kw, cat, year):
                    break
            if not search_result:
                logger.warning(f"No search results: {file_title=} {year=}")
                return {}
            if isinstance(search_result, list) and search_result:
                first_result = search_result[0]
            # KTV 서치 목록
            elif isinstance(search_result, dict):
                default_site = search_result.get("daum") or {}
                if self.settings.broadcast.is_relative_ott(path):
                    if path.stem.endswith("-SW"):
                        ott_site = "wavve"
                    elif path.stem.endswith("-ST"):
                        ott_site = "tving"
                    else:
                        ott_site = None
                    if ott_site and (ott_result := search_result.get(ott_site)):
                        logger.info(f"Relative site: {ott_site}")
                        default_site = ott_result

                # fallback
                if not default_site:
                    for result in search_result.values():
                        if result:
                            default_site = result
                            break

                # Daum은 dict, 나머지는 list
                if isinstance(default_site, list) and default_site:
                    first_result = default_site[0]
                elif isinstance(default_site, dict):
                    first_result = default_site
                else:
                    first_result = {}
            else:
                first_result = {}
            if code := first_result.get("code"):
                return await self._lookup_metadata(code)
            else:
                logger.warning(f"No code: {file_title=} {first_result=}")
                return {}

    async def _search_metadata(
        self, keyword: str, category: str = "ktv", year: int = 1900
    ) -> dict | list:
        logger.debug(f"Search metadata: {keyword=} {category=}")
        if not self.session:
            logger.error("Session is not initialized...")
            return {}
        api_path = f"/metadata/api/{category}/search"
        query = {
            "call": "plex",
            "manual": "True",
            "keyword": keyword,
            "year": year,
        }
        url = urljoin(self.settings.flaskfarm.url, f"{api_path}?{urlencode(query)}")
        try:
            async with self.session.post(
                url, data={"apikey": self.settings.flaskfarm.apikey}
            ) as response:
                search_result = await response.json()
                if search_result:
                    return search_result
        except Exception:
            logger.exception(
                f"Metadata searching failed: {keyword=} {category=} {year=}"
            )
        return {}

    async def _lookup_metadata(self, code: str) -> dict:
        if not isinstance(code, str) or len(code) < 1:
            logger.warning(f"{code=}")
            return {}
        match code[0]:
            case "M":
                category = "movie"
            case "F":
                category = "ftv"
            case _:
                category = "ktv"
        logger.debug(f"Lookup metadata: {code=} {category=}")
        if not self.session:
            logger.error("Session is not initialized...")
            return {}
        api_path = f"/metadata/api/{category}/info"
        query = {
            "call": "plex",
            "manual": "True",
            "code": code,
        }
        url = urljoin(self.settings.flaskfarm.url, f"{api_path}?{urlencode(query)}")
        try:
            async with self.session.post(
                url, data={"apikey": self.settings.flaskfarm.apikey}
            ) as response:
                return await response.json()
        except Exception:
            logger.exception(f"Metadata lookup failed: {code=}")
        return {}

    def _build_movie_data(
        self,
        metadata: dict,
        path: Path,
        item: str,
        module: str,
        file_title: str,
        file_count: int = 0,
        total_size: int = 0,
        parsed: dict = {},
    ) -> dict:
        metadata = metadata or {}
        countries = metadata.get("country") or []
        ca = "Unknown"
        if self.settings.broadcast.get_genre_from_subfolder(path) == "최신":
            ca = "최신"
        elif countries:
            for korea in ("한국", "대한민국", "Korea"):
                if korea in countries:
                    ca = "한국"
                    break
            else:
                ca = "외국"
        return {
            "t1": "bot_downloader",
            "t2": module,
            "data": {
                "ca": ca,
                "count": file_count,
                "folderid": item,
                "foldername": path.name,
                "meta": {
                    "code": metadata.get("code") or "Unknown",
                    # 검색 결과는 장르와 국가 정보가 없음
                    "country": countries,
                    "genre": metadata.get("genre") or [],
                    "originaltitle": metadata.get("originaltitle")
                    or metadata.get("title_original")
                    or "",
                    "poster": metadata.get("main_poster")
                    or metadata.get("image_url")
                    or self.settings.images.no_poster,
                    "title": metadata.get("title")
                    or metadata.get("title_en")
                    or "Unknown",
                    "year": metadata.get("year", 1900),
                },
                "size": total_size,
                "subject": file_title,
            },
        }

    def _build_vod_data(
        self,
        metadata: dict,
        path: Path,
        item: str,
        module: str,
        file_title: str,
        file_count: int = 0,
        total_size: int = 0,
        parsed: dict = {},
    ) -> dict:
        metadata = metadata or {}
        date_match = re.search(r"\d{6}", path.stem)
        genres = metadata.get("genre")
        if folder_genre := self.settings.broadcast.get_genre_from_subfolder(path):
            genre = folder_genre
        elif isinstance(genres, list) and genres:
            genre = genres[0]
        else:
            genre = "Unknown"
        poster = None
        image_list = metadata.get("thumb") or metadata.get("art")
        if isinstance(image_list, list):
            sorted_image_list = sorted(
                image_list,
                key=lambda x: (x.get("aspect") == "poster", x.get("score") or 0),
                reverse=True,
            )
            if selected := next(iter(sorted_image_list), None):
                poster = selected.get("value") or selected.get("thumb")
        else:
            poster = metadata.get("main_poster") or metadata.get("image_url")
        if not poster:
            poster = self.settings.images.no_poster
        return {
            "t1": "bot_downloader",
            "t2": module,
            "data": {
                "f": path.name,
                "id": item,
                "meta": {
                    "code": metadata.get("code") or "Unknown",
                    "genre": genre,
                    "poster": poster,
                    "title": metadata.get("title") or "Unknown",
                },
                "s": total_size,
                "c": file_count,
                "vod": {
                    "date": date_match.group() if date_match else "",
                    "name": file_title,
                    "no": parsed.get("episode") or 0,
                    "quality": (parsed.get("resolution") or "").strip("p")
                    or parsed.get("quality")
                    or "",
                    "release": parsed.get("encoder") or "",
                },
            },
        }

    def _pad(self, text: str) -> bytes:
        text_bytes = text.encode("utf-8")
        pad_len = AES.block_size - (len(text_bytes) % AES.block_size)
        padding = bytes([pad_len] * pad_len)
        return text_bytes + padding

    def _unpad(self, padded_data: bytes) -> bytes:
        pad_len = padded_data[-1]
        return padded_data[:-pad_len]

    def encrypt(self, content: str, key: str) -> str:
        content_bytes = self._pad(content)
        key_bytes = key.encode()
        iv = Random.new().read(AES.block_size)
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
        encrypted_bytes = cipher.encrypt(content_bytes)
        result = base64.b64encode(iv + encrypted_bytes)
        return result.decode()

    def decrypt(self, encoded: str, key: str) -> str:
        decoded_bytes = base64.b64decode(encoded)
        iv = decoded_bytes[: AES.block_size]
        if len(iv) != AES.block_size:
            iv = os.urandom(AES.block_size)
        encrypted_content = decoded_bytes[AES.block_size :]
        key_bytes = key.encode()
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
        decrypted_bytes = cipher.decrypt(encrypted_content)
        unpadded_bytes = self._unpad(decrypted_bytes)
        return unpadded_bytes.decode()
