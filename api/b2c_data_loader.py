import json
import os
import re
from collections import defaultdict


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
            self._data = None
            return
        self._data = json.loads(m.group(1))
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
                    week_to: int = 52, channel: str = None,
                    category: str = None) -> dict | None:
        self._load()
        if not self._data or year not in self._data.get("data", {}):
            return None

        raw = self._data["data"][year].get("raw", [])
        meta = self._data["data"][year].get("meta", {})

        by_week = defaultdict(lambda: {"qty": 0, "channels": defaultdict(int)})
        by_category = defaultdict(int)
        total = 0

        for r in raw:
            wnum = int(r["w"][1:])
            if not (week_from <= wnum <= week_to):
                continue
            if channel and r["ch"] != channel:
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
