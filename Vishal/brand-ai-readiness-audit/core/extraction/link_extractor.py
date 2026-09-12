from bs4 import BeautifulSoup
from typing import List, Dict

class LinkExtractor:
    def __init__(self, html: str, base_url: str):
        self.soup = BeautifulSoup(html, "html.parser")
        self.base_url = base_url

    def get_links(self) -> List[Dict[str, str]]:
        links = []
        for a_tag in self.soup.find_all("a", href=True):
            links.append({
                "text": a_tag.get_text(strip=True),
                "href": a_tag["href"]
            })
        return links
