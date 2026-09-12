#!/usr/bin/env python3
"""
LLM Visibility Agent — Phase 3 implementation.

This agent generates prompts related to a brand, queries an LLM adapter
(mocked by default), parses the responses for mentions, citations, and recommendations,
and emits structured findings compatible with the shared findings format.
"""

import sys
import os
import json
import re
from typing import List, Dict, Any

# Ensure we can import shared.py from the lib directory
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'lib'))
from shared import make_finding

class PromptGenerator:
    """Generates a standard set of prompts across the customer journey."""
    def __init__(self, brand: str, category: str = "software"):
        self.brand = brand
        self.category = category

    def generate_prompts(self) -> List[Dict[str, str]]:
        return [
            {"intent": "Informational", "text": f"What is {self.brand} known for?"},
            {"intent": "Problem-solving", "text": f"How do I solve common issues with {self.brand}?"},
            {"intent": "Comparative", "text": f"What are the best alternatives to {self.brand}?"},
            {"intent": "Commercial", "text": f"Best {self.category} providers in 2026"},
            {"intent": "Trust", "text": f"Is {self.brand} reliable and secure?"}
        ]

class LLMAdapter:
    """Base class for querying LLMs."""
    def query(self, prompt: str) -> str:
        raise NotImplementedError

class MockAdapter(LLMAdapter):
    """Simulates an LLM response for testing purposes."""
    def __init__(self, brand: str, domain: str):
        self.brand = brand
        self.domain = domain

    def query(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        if "what is" in prompt_lower:
            return f"{self.brand} is a leading provider. [1]\n\n[1] https://{self.domain}/about"
        elif "alternatives" in prompt_lower:
            return f"Some alternatives to {self.brand} include CompetitorA and CompetitorB. However, {self.brand} is often preferred. [1]\n\n[1] https://competitor-review-site.com/compare"
        elif "providers" in prompt_lower:
            # Simulate a scenario where the brand is NOT mentioned for generic commercial queries
            return f"The top providers are CompetitorA, CompetitorB, and CompetitorC. [1]\n\n[1] https://industry-analyst.com/top-10"
        else:
            return f"{self.brand} is generally well-regarded. [1]\n\n[1] https://{self.domain}/docs"

class ResponseParser:
    """Extracts mentions, citations, and recommendations from LLM text."""
    def __init__(self, brand: str, domain: str):
        self.brand = brand
        self.domain = domain
        self.competitor_pattern = re.compile(r'Competitor[A-Z]') # Simple mock competitor pattern

    def parse(self, text: str) -> Dict[str, Any]:
        # Extract mentions
        mentions = text.lower().count(self.brand.lower())
        
        # Extract citations (look for http/https URLs)
        urls = re.findall(r'https?://[^\s\]]+', text)
        owned_citations = [u for u in urls if self.domain in u]
        third_party_citations = [u for u in urls if self.domain not in u]
        
        # Extract recommendations (competitors)
        recommendations = list(set(self.competitor_pattern.findall(text)))
        
        return {
            "mentions": mentions,
            "owned_citations": owned_citations,
            "third_party_citations": third_party_citations,
            "recommendations": recommendations,
            "raw_text": text
        }

def run_audit(brand: str, domain: str) -> List[Dict[str, Any]]:
    findings = []
    
    generator = PromptGenerator(brand)
    prompts = generator.generate_prompts()
    
    adapter = MockAdapter(brand, domain)
    parser = ResponseParser(brand, domain)
    
    results = []
    for prompt_obj in prompts:
        response_text = adapter.query(prompt_obj["text"])
        parsed = parser.parse(response_text)
        results.append({
            "prompt": prompt_obj["text"],
            "intent": prompt_obj["intent"],
            "parsed": parsed
        })
        
    # Analyze aggregated results
    total_prompts = len(prompts)
    mentioned_count = sum(1 for r in results if r["parsed"]["mentions"] > 0)
    owned_citation_count = sum(1 for r in results if len(r["parsed"]["owned_citations"]) > 0)
    generic_commercial_runs = [r for r in results if r["intent"] == "Commercial"]
    
    # AI-001: Mention Rate
    if mentioned_count < total_prompts * 0.8:
        findings.append(make_finding(
            "AI-001", "Low LLM Mention Rate", "high",
            f"The brand was mentioned in only {mentioned_count} out of {total_prompts} prompts. Expected >= 80%.",
            "Improve off-site authority and ensure brand name is strongly associated with core topics.",
            affected_urls=[f"prompt:{r['prompt']}" for r in results if r["parsed"]["mentions"] == 0]
        ))

    # AI-002: Owned Citation Rate
    if owned_citation_count < total_prompts * 0.5:
        findings.append(make_finding(
            "AI-002", "Low Owned-Domain Citation Rate", "medium",
            f"The LLM cited the owned domain ({domain}) in only {owned_citation_count} out of {total_prompts} prompts.",
            "Create comprehensive, original source material that LLMs prefer to cite over third-party aggregators.",
            affected_urls=[domain]
        ))
        
    # AI-003: Commercial Share of Voice
    for run in generic_commercial_runs:
        if run["parsed"]["mentions"] == 0 and len(run["parsed"]["recommendations"]) > 0:
            findings.append(make_finding(
                "AI-003", "Missing from Commercial Recommendations", "high",
                f"For the prompt '{run['prompt']}', the LLM recommended {', '.join(run['parsed']['recommendations'])} but completely omitted {brand}.",
                "Publish comparative content and ensure third-party industry lists include the brand.",
                affected_urls=[f"prompt:{run['prompt']}"]
            ))

    return findings

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="LLM Visibility Agent")
    parser.add_argument("--brand", required=True, help="Target Brand Name")
    parser.add_argument("--domain", required=True, help="Target Owned Domain")
    args = parser.parse_args()
    
    all_findings = run_audit(args.brand, args.domain)
    print(json.dumps(all_findings, indent=2, ensure_ascii=False))
