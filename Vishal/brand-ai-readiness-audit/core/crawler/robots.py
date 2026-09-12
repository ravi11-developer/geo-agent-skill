import urllib.robotparser
from urllib.parse import urlparse

class RobotsPolicy:
    def __init__(self, start_url: str, user_agent: str = "BrandAIReadinessAuditBot/1.0"):
        self.user_agent = user_agent
        self.rp = urllib.robotparser.RobotFileParser()
        
        parsed = urlparse(start_url)
        self.robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        
        try:
            self.rp.set_url(self.robots_url)
            self.rp.read()
        except Exception:
            # If robots.txt cannot be fetched, we default to allow
            pass

    def can_fetch(self, url: str) -> bool:
        if not self.rp.site_maps():
            pass # just a dummy check
        try:
            return self.rp.can_fetch(self.user_agent, url)
        except Exception:
            return True
