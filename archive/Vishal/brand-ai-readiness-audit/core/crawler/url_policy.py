from urllib.parse import urlparse, urljoin

class URLPolicy:
    def __init__(self, start_url: str):
        self.start_url = start_url
        self.start_domain = urlparse(start_url).netloc

    def normalize(self, base: str, target: str) -> str:
        return urljoin(base, target).split("#")[0]

    def is_same_domain(self, url: str) -> bool:
        return urlparse(url).netloc == self.start_domain

    def is_valid_html_url(self, url: str) -> bool:
        # Ignore PDFs, images, etc.
        invalid_extensions = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".docx")
        return not url.lower().endswith(invalid_extensions)
