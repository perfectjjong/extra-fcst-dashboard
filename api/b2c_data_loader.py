import json
import logging
import os
import re
from collections import defaultdict

logger = logging.getLogger(__name__)


class B2CDataLoader:

    def __init__(self, html_path: str):
        self._path = html_path
        self._data = None
        self._mtime = 0
        self._load()

    def _load(self):
        if not os.path.exists(self._path):
            self._data = None
            return
        mtime = os.path.getmtime(self._path)
        if self._data and mtime == self._mtime:
            return
        with open(self._path, encoding="utf-8") as f:
            html = f.read()
        m = re.search(r'const\s+_ALL\s*=\s*(\{.*?\});\s*\n', html, re.DOTALL)
        if not m:
            logger.warning("B2C HTML에서 _ALL 데이터를 찾을 수 없습니다: %s", self._path)
            self._data = None
            return
        try:
            self._data = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            logger.warning("B2C _ALL JSON 파싱 실패: %s — %s", self._path, e)
            self._data = None
            return
        self._mtime = mtime

    @property
    def years(self):
        self._load()
        return self._data["years"] if self._data else []

    @property
    def current_year(self):
        self._load()
        return self._data.get("current") if self._data else None

    def get_sellout(self, year: str = "2026", week_from: int = 1,
                    week_to: int = 52, channel=None,
                    category: str = None) -> dict | None:
        """channel: str (단일) 또는 list/set (복수 채널 중 하나라도 포함)."""
        self._load()
        if not self._data or year not in self._data.get("data", {}):
            return None

        raw = self._data["data"][year].get("raw", [])
        meta = self._data["data"][year].get("meta", {})

        channel_set = None
        if channel:
            channel_set = set(channel) if not isinstance(channel, str) else {channel}

        by_week = defaultdict(lambda: {"qty": 0, "channels": defaultdict(int)})
        by_category = defaultdict(int)
        total = 0

        for r in raw:
            wnum = int(r["w"][1:])
            if not (week_from <= wnum <= week_to):
                continue
            if channel_set and r["ch"] not in channel_set:
                continue
            if category and r["c"] != category:
                continue
            q = r.get("q", 0)
            total += q
            by_week[r["w"]]["qty"] += q
            by_week[r["w"]]["channels"][r["ch"]] += q
            by_category[r["c"]] += q

        return {
            "year": year,
            "total_qty": total,
            "by_week": {k: {"qty": v["qty"], "channels": dict(v["channels"])}
                        for k, v in sorted(by_week.items(),
                                           key=lambda x: int(x[0][1:]))},
            "by_category": dict(by_category),
            "data_as_of": meta.get("generated", "unknown"),
        }
