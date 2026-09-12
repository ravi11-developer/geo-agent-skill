import requests
from typing import List, Dict, Set
from .robots import RobotsPolicy
from .url_policy import URLPolicy

class SimpleCrawler:
    def __init__(self, start_url: str, max_pages: int = 5):
        self.start_url = start_url
        self.max_pages = max_pages
        self.robots = RobotsPolicy(start_url)
        self.policy = URLPolicy(start_url)
        self.visited: Set[str] = set()
        self.pages: Dict[str, str] = {} # url -> html content

    def fetch_page(self, url: str) -> str:
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.text
        except Exception as e:
            return ""

    def crawl(self) -> Dict[str, str]:
        queue = [self.start_url]
        
        while queue and len(self.visited) < self.max_pages:
            current_url = queue.pop(0)
            
            if current_url in self.visited:
                continue
                
            if not self.policy.is_same_domain(current_url):
                continue
                
            if not self.robots.can_fetch(current_url):
                continue
                
            html = self.fetch_page(current_url)
            self.visited.add(current_url)
            
            if html:
                self.pages[current_url] = html
                # A real crawler would extract links here and add to queue
                
        return self.pages
