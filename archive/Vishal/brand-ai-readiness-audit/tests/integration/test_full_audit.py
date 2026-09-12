import pytest
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from core.extraction.metadata_extractor import MetadataExtractor

def test_metadata_extraction_on_clear_site():
    with open('tests/fixtures/clear_entity_site/index.html', 'r') as f:
        html = f.read()
    
    extractor = MetadataExtractor(html)
    json_ld = extractor.get_json_ld()
    
    assert len(json_ld) == 1
    assert json_ld[0]['@type'] == 'Organization'
    assert json_ld[0]['name'] == 'Clear Entity Inc'

def test_metadata_extraction_on_js_site():
    with open('tests/fixtures/js_only_site/index.html', 'r') as f:
        html = f.read()
    
    extractor = MetadataExtractor(html)
    json_ld = extractor.get_json_ld()
    
    # Should not find JSON-LD in the raw HTML of the JS site
    assert len(json_ld) == 0
