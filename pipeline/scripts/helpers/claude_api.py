"""Claude API helper for content analysis and generation tasks."""

import json
import os
from pathlib import Path

from anthropic import Anthropic


def get_client() -> Anthropic:
    """Return an Anthropic client. Expects ANTHROPIC_API_KEY env var."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Set it with: set ANTHROPIC_API_KEY=your-key-here"
        )
    return Anthropic(api_key=api_key)


def analyze_transcript(transcript_text: str, summary_text: str = "", episode_id: str = "") -> dict:
    """Send a transcript to Claude for content analysis.

    Returns a structured analysis dict with topics, highlights, arc, etc.
    """
    client = get_client()

    system_prompt = """You are an expert podcast producer and content curator.
You analyze raw conversation transcripts and identify the most compelling,
insightful, and entertaining segments for a curated podcast episode.

The podcast is called "Two Brooklyn Guys" — two friends discussing AI,
technology, and life. The tone is conversational, insightful, and authentic.

Always respond with valid JSON only, no markdown fencing."""

    user_prompt = f"""Analyze this transcript from episode {episode_id} of "Two Brooklyn Guys" podcast.

{f"EXISTING SUMMARY: {summary_text}" if summary_text else ""}

TRANSCRIPT:
{transcript_text}

Return a JSON object with this exact structure:
{{
  "suggested_titles": [
    "Title Option 1 (keyword-rich, under 70 chars)",
    "Title Option 2",
    "Title Option 3"
  ],
  "topics": [
    {{
      "id": "T1",
      "title": "Topic title",
      "start_segment": 1,
      "end_segment": 5,
      "summary": "Brief summary of this topic section",
      "energy_level": "low|medium|high",
      "recommendation": "keep_full|trim_to_highlights|cut"
    }}
  ],
  "highlights": [
    {{
      "id": "H1",
      "segment_ids": [3, 4],
      "speaker": "Speaker name",
      "quote": "The exact compelling quote or paraphrase",
      "type": "insight|advice|story|humor|debate|emotional",
      "cold_open_candidate": true,
      "social_clip_candidate": true,
      "why_compelling": "Brief explanation of why this stands out"
    }}
  ],
  "suggested_arc": ["T2", "T5", "T1", "T3"],
  "suggested_cuts": [
    {{
      "segment_ids": [10, 11, 12],
      "reason": "Off-topic tangent about..."
    }}
  ],
  "chapters": [
    {{
      "title": "Chapter title",
      "start_segment": 1
    }}
  ],
  "key_takeaways": [
    "Takeaway 1",
    "Takeaway 2",
    "Takeaway 3"
  ],
  "episode_summary": "A 2-3 sentence compelling summary for the episode description",
  "mood": "informative|casual|debate|storytelling|mixed",
  "content_warnings": []
}}

Important:
- Be selective with highlights — pick only the truly standout moments (3-8 per episode)
- suggested_arc should reorder topics for maximum narrative impact
- Cold open candidates should be punchy, intriguing, and work as standalone hooks
- Social clip candidates should be self-contained and impactful in 30-60 seconds
- Segment IDs reference the transcript segment numbers"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
    )

    response_text = message.content[0].text

    # Parse JSON from response (handle potential markdown fencing)
    text = response_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    return json.loads(text)


def generate_show_notes(
    transcript_text: str,
    analysis: dict,
    episode_id: str = "",
    episode_title: str = "",
) -> dict:
    """Generate show notes, social posts, and SEO content from a transcript and analysis."""
    client = get_client()

    system_prompt = """You are an expert podcast content writer who creates
compelling show notes, social media posts, and SEO content for the
"Two Brooklyn Guys" podcast. Always respond with valid JSON only."""

    user_prompt = f"""Generate content for episode {episode_id}: "{episode_title}"

ANALYSIS:
{json.dumps(analysis, indent=2)}

TRANSCRIPT (for reference):
{transcript_text[:8000]}

Return a JSON object:
{{
  "show_notes_md": "Full show notes in Markdown with: hook opening, timestamps, key takeaways, resources mentioned, CTA",
  "episode_description": "150-300 word description for podcast directories",
  "social_posts": {{
    "twitter": "Tweet under 280 chars with hook + link placeholder",
    "linkedin": "Professional LinkedIn post, 2-3 paragraphs",
    "threads": "Casual Threads post"
  }},
  "seo": {{
    "meta_title": "SEO-optimized page title (under 60 chars)",
    "meta_description": "Meta description (under 160 chars)",
    "keywords": ["keyword1", "keyword2"]
  }}
}}"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
    )

    text = message.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    return json.loads(text)
