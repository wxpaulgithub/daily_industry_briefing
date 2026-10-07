"""Reuse project skills as raw candidates, without the news ranking pipeline."""
import asyncio

from ..models import OpportunityCandidate


class ExistingSkillsSource:
    async def fetch(self, client):
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
