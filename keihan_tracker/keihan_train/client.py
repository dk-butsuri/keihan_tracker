"""
公式APIから本文を取得する。取得した本文（str）か、変わっていなければ None を返すだけで、列車の状態は持たない。
"""

from httpx import AsyncClient, Response
from pathlib import Path
from typing import Optional
import asyncio
import datetime
import gzip
import warnings

from .state import JST

# 京阪のサーバーから、どこのプログラムのアクセスか分かるように名乗る
USER_AGENT = "keihan_tracker (+https://github.com/dk-butsuri/keihan_tracker)"


async def write_snapshot(snapshot_dir: Path, name: str, body: bytes, last: dict[str, bytes]) -> None:
    """
    取得したレスポンスを snapshot_dir/YYYYMMDD/HHMMSS.mmm_name.gz に保存する（デバッグ用）。
    last[name] と同じ内容なら保存しない。保存できたら last[name] を更新する。
    """
    if last.get(name) == body:
        return
    now = datetime.datetime.now(JST)
    path = snapshot_dir / now.strftime("%Y%m%d") / f"{now:%H%M%S}.{now.microsecond // 1000:03}_{name}.gz"

    def write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(body))

    # 保存の失敗で取得を止めない
    try:
        await asyncio.to_thread(write)
    except OSError as e:
        warnings.warn(f"スナップショットの保存に失敗しました: {e}")
        return
    last[name] = body


class KHClient:
    """
    京阪の公式APIを取得するクライアント。状態は持たない。
    - web: 通信に使う httpx.AsyncClient
    - snapshot_dir を指定すると、取得したAPIレスポンスをそのまま保存する（デバッグ用）
    """
    def __init__(self, snapshot_dir: Optional[str | Path] = None) -> None:
        self.web = AsyncClient(headers={"User-Agent": USER_AGENT})
        # 取得したレスポンスの保存先（Noneなら保存しない）
        self.snapshot_dir: Optional[Path] = Path(snapshot_dir) if snapshot_dir is not None else None
        self._last_snapshot: dict[str, bytes] = {}                       # ファイル名:前回保存した内容
        self._etags: dict[str, str] = {}                                 # URL:前回受け取ったETag

    async def _get(self, url: str) -> str:
        """URLを取得して本文を返す。snapshot_dirが指定されていれば保存もする。"""
        res = await self._request(url, {})
        return res.text

    async def _get_if_modified(self, url: str) -> Optional[str]:
        """前回の取得から変わっていれば本文を返し、変わっていなければ（304 Not Modified）Noneを返す。"""
        etag = self._etags.get(url)
        res = await self._request(url, {"If-None-Match": etag} if etag else {})
        if res.status_code == 304:
            return None
        return res.text

    async def _request(self, url: str, headers: dict[str, str]) -> Response:
        res = await self.web.get(url, headers=headers)
        if res.status_code == 304:
            return res
        res.raise_for_status()
        # 次回の条件付き取得のために覚えておく
        if etag := res.headers.get("ETag"):
            self._etags[url] = etag
        if self.snapshot_dir is not None:
            await self._save_snapshot(url.rsplit("/", 1)[-1], res.content)
        return res

    async def _save_snapshot(self, name: str, body: bytes) -> None:
        """前回から内容が変わっていれば snapshot_dir/YYYYMMDD/HHMMSS.mmm_name.gz に保存する。"""
        assert self.snapshot_dir is not None
        await write_snapshot(self.snapshot_dir, name, body, self._last_snapshot)

    # 公開名。本文を返すだけなので、パースは parse.py の関数に渡す
    get = _get
    get_if_modified = _get_if_modified
