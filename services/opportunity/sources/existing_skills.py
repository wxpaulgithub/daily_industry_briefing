"""Reuse project skills as raw candidates, without the news ranking pipeline."""
import asyncio

from ..models import OpportunityCandidate


class ExistingSkillsSource:
    def __init__(self, offline=False):
        self.offline = offline

    async def fetch(self, client):
        if self.offline:
            # Reuse saved news instead of launching unmetered nested search skills.
            from config import OUTPUT_DIR
            from ..runtime import read_json
            rows = []
            for path in sorted(OUTPUT_DIR.glob("????-??-??.json"), reverse=True)[:3]:
                data = read_json(path, [])
                if isinstance(data, list):
                    rows.extend(data)
            allowed = OpportunityCandidate.__dataclass_fields__
            return [OpportunityCandidate(**{key: value for key, value in row.items() if key in allowed})
                    for row in rows if isinstance(row, dict) and row.get("title") and row.get("url")]

        from services.skills.bidding import BiddingSkill
        from services.skills.local_projects import LocalProjectSkill
        from services.skills.toutiao import ToutiaoSkill
        skills = [BiddingSkill(), LocalProjectSkill(), ToutiaoSkill()]
        batches = await asyncio.gather(*(asyncio.wait_for(skill.fetch_all(client), 45) for skill in skills), return_exceptions=True)
        candidates = []
        for batch in batches:
            if isinstance(batch, BaseException):
                continue
            for article in batch:
                candidates.append(OpportunityCandidate(
                    title=article.title, url=article.url, summary=article.summary,
                    source_name=article.source_name, published=article.published,
                    published_ts=article.published_ts,
                ))
        return candidates
