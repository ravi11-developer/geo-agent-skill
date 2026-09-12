from bs4 import BeautifulSoup
from typing import List
import re

class HTMLExtractor:
    def __init__(self, html: str):
        self.soup = BeautifulSoup(html, "html.parser")

    def get_text(self) -> str:
        """Extracts visible text, ignoring scripts and styles."""
        for script in self.soup(["script", "style", "noscript"]):
            script.extract()
        text = self.soup.get_text(separator=" ", strip=True)
        return text

    def get_word_count(self) -> int:
        """Returns the number of words in the visible text."""
        text = self.get_text()
        return len(text.split())

    def get_headings(self) -> List[str]:
        headings = []
        for tag in ["h1", "h2", "h3"]:
            for h in self.soup.find_all(tag):
                headings.append(h.get_text(strip=True))
        return headings

    def has_navigation(self) -> bool:
        """Checks if the page has basic navigation elements or links."""
        navs = self.soup.find_all("nav")
        if len(navs) > 0:
            return True
        links = self.soup.find_all("a")
        return len(links) > 3

    def has_noindex(self) -> bool:
        """Checks if there is a meta robots noindex tag."""
        for meta in self.soup.find_all("meta", attrs={"name": re.compile(r"robots", re.I)}):
            if "noindex" in str(meta.get("content", "")).lower():
                return True
        return False

    def get_title(self) -> str:
        """Returns the title of the page."""
        title_tag = self.soup.find("title")
        if title_tag:
            return title_tag.get_text(strip=True)
        return ""

    def has_stale_years(self) -> bool:
        """Checks if there are old years mentioned as current."""
        text = self.get_text()
        # Look for copyright years older than 2025
        if re.search(r'(?:©|Copyright)\s*(?:201\d|2020|2021|2022|2023|2024)', text, re.I):
            return True
        return False

    def get_copyright_name(self) -> str:
        """Extracts the company name from the copyright string."""
        text = self.get_text()
        m = re.search(r'(?:©|Copyright)\s*(?:20\d\d)?\s*([A-Za-z\s\,]+?)(?:\.|All rights)', text, re.I)
        if m:
            return m.group(1).strip()
        return ""

    def has_images_without_alt(self) -> bool:
        """Checks if there are images without alt text."""
        images = self.soup.find_all("img")
        for img in images:
            alt = img.get("alt", "")
            if not alt or alt.strip() == "":
                return True
        return False

    def get_image_count(self) -> int:
        return len(self.soup.find_all("img"))
