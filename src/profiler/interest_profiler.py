import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from config import settings
from .daily_scanner import DailyScanner
from .rag_checker import RAGChecker


class UserProfile(BaseModel):
    core_interests: List[str] = Field(description="최근 집중하고 있는 핵심 기술/학습 주제 목록")
    knowledge_depth: Dict[str, str] = Field(description="주제별 사용자가 이미 보유한 지식 수준 및 구현 경험 요약")
    avoid_topics: List[str] = Field(description="이미 숙지하여 추천에서 제외해야 할 기초/입문용 주제")
    target_domains: List[str] = Field(description="관심 있는 주요 기술 분야 (예: AI Agent, Memory Optimization 등)")
    search_keywords: List[str] = Field(description="트렌드 검색에 직접 사용할 수 있는 영문/한글 키워드 목록")


class InterestProfiler:
    """Extracts user interests from recent notes and evaluates knowledge depth via RAG."""

    def __init__(self, vault_path: Optional[Path] = None, daemon_url: Optional[str] = None):
        self.vault_path = vault_path or settings.obsidian_vault_path
        self.daemon_url = daemon_url or settings.qdrant_rag_url
        self.scanner = DailyScanner(self.vault_path)
        self.rag = RAGChecker(self.vault_path, self.daemon_url)

    def _call_llm(self, prompt: str) -> str:
        """Call LLM via Gemini or OpenAI."""
        if settings.gemini_api_key:
            from google import genai
            client = genai.Client(api_key=settings.gemini_api_key)
            models_to_try = [settings.gemini_model, "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"]
            # Deduplicate while preserving order
            models_to_try = list(dict.fromkeys(models_to_try))
            for model_name in models_to_try:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt
                    )
                    if response.text:
                        return response.text
                except Exception as e:
                    print(f"[InterestProfiler] Gemini {model_name} failed: {e}. Trying next fallback...")



        if settings.openai_api_key:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=settings.openai_api_key)
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}]
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                print(f"[InterestProfiler] OpenAI API call failed: {e}")

        # Fallback dummy profile if no API key is set yet
        print("[InterestProfiler] No active LLM API key. Returning heuristic profile.")
        return json.dumps({
            "core_interests": [
                "MCP (Model Context Protocol) Daemon Architecture",
                "PyTorch VRAM Optimization & Windows WDDM Paging",
                "Qdrant Vector Database & Hybrid Search",
                "AI Agent Frameworks & Automation",
                "Jekyll GitHub Pages Personal Site Unification"
            ],
            "knowledge_depth": {
                "MCP": "Expert: Implemented single SSE daemon + stdio proxy with 66% RAM reduction",
                "PyTorch/VRAM": "Advanced: Deep understanding of WDDM shared GPU memory paging and freeze mitigation",
                "Qdrant": "Advanced: Multi-model embedding store, batch upserting, Docker deployment"
            },
            "avoid_topics": [
                "Docker basics",
                "What is MCP",
                "Intro to Vector DB",
                "Jekyll hello world"
            ],
            "target_domains": [
                "AI Agent Systems",
                "Memory & Performance Optimization",
                "Developer Tooling & Infrastructure"
            ],
            "search_keywords": [
                "MCP server",
                "Model Context Protocol",
                "PyTorch memory optimization",
                "Qdrant hybrid search",
                "AI agent workflow",
                "vLLM memory",
                "fastembed"
            ]
        }, ensure_ascii=False)

    def build_profile(self, days: int = 14) -> UserProfile:
        """Build a comprehensive UserProfile based on recent notes and RAG depth."""
        daily_notes = self.scanner.get_recent_daily_notes(days=days)
        active_projects = self.scanner.get_active_projects()

        # Aggregate summary of recent notes
        recent_summaries = []
        for note in daily_notes[:5]:
            snippet = note["content"][:1500]  # First 1500 chars of each note
            recent_summaries.append(f"### [일기: {note['date']}]\n{snippet}\n")

        for proj in active_projects[:3]:
            snippet = proj["content"][:1000]
            recent_summaries.append(f"### [프로젝트: {proj['name']}]\n{snippet}\n")

        notes_context = "\n".join(recent_summaries)

        prompt = f"""
다음은 사용자의 최근 일기 및 진행 중인 프로젝트 기록이다.
사용자의 최근 관심사를 분석하고, 사용자가 이미 높은 수준으로 구현하거나 이해하고 있는 지식 깊이를 파악하여
아래 JSON 형식에 맞추어 출력하라. 마크다운 코드블록(```json ... ```)을 포함해 출력하라.

[최근 기록]
{notes_context}

[JSON 요구 스키마]
{{
  "core_interests": ["최근 집중하는 기술/학습 주제 5~7개"],
  "knowledge_depth": {{"주제명": "사용자가 이미 달성한 지식/구현 수준 요약"}},
  "avoid_topics": ["이미 마스터했으므로 추천에서 제외해야 할 기초/입문/상식적 주제들"],
  "target_domains": ["관심 있는 기술 분야 3~5개"],
  "search_keywords": ["GitHub Trending, GeekNews, Hacker News 등에서 검색/필터링할 영문/한글 키워드 8~12개"]
}}
"""
        raw_output = self._call_llm(prompt)
        
        # Parse JSON
        try:
            cleaned = raw_output.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```")[1].split("```")[0].strip()
            data = json.loads(cleaned)
            return UserProfile(**data)
        except Exception as e:
            print(f"[InterestProfiler] Failed to parse LLM JSON: {e}. Raw: {raw_output[:200]}")
            # Fallback
            return UserProfile(
                core_interests=["MCP Daemon", "Qdrant RAG", "PyTorch VRAM", "AI Agent Tooling"],
                knowledge_depth={"MCP": "Advanced single SSE daemon implementation"},
                avoid_topics=["Intro to Docker", "Intro to Python"],
                target_domains=["AI Engineering", "Performance Optimization"],
                search_keywords=["mcp", "qdrant", "pytorch vram", "ai agent", "llm tooling"]
            )
