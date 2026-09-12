from bs4 import BeautifulSoup
from typing import Dict, Any, List
import json

class MetadataExtractor:
    def __init__(self, html: str):
        self.soup = BeautifulSoup(html, "html.parser")

    def get_meta_tags(self) -> Dict[str, str]:
        meta_data = {}
        for meta in self.soup.find_all("meta"):
            name = meta.get("name", meta.get("property"))
            content = meta.get("content")
            if name and content:
                meta_data[name] = content
        return meta_data

    def get_json_ld(self) -> List[Dict[str, Any]]:
        json_ld_data = []
        for script in self.soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    json_ld_data.extend(data)
                else:
                    json_ld_data.append(data)
            except Exception:
                pass
        return json_ld_data
