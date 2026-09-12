class DOMCompare:
    @staticmethod
    def compare(raw_html: str, rendered_html: str) -> dict:
        """
        Compares raw HTML (from requests) with rendered HTML (from browser)
        to identify heavy JavaScript reliance.
        """
        raw_len = len(raw_html)
        rendered_len = len(rendered_html)
        
        difference = abs(rendered_len - raw_len)
        percentage_diff = (difference / max(raw_len, 1)) * 100
        
        return {
            "raw_size_bytes": raw_len,
            "rendered_size_bytes": rendered_len,
            "difference_bytes": difference,
            "percentage_difference": percentage_diff,
            "is_js_heavy": percentage_diff > 50  # Arbitrary threshold
        }
