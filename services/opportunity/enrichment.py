"""Rule-based field extraction. V1 intentionally does not use an LLM."""
import re


PROVINCES = ("江苏", "上海", "浙江", "安徽", "山东", "广东", "福建", "湖北", "北京", "天津", "重庆", "四川")
CITIES = ("无锡", "苏州", "常州", "南京", "南通", "上海", "杭州", "宁波", "合肥", "青岛", "济南", "深圳", "广州", "武汉")


def extract_location(text: str) -> tuple[str, str]:
    province = next((x for x in PROVINCES if x in text), "")
    city = next((x for x in CITIES if x in text), "")
    return province, city


def extract_budget(text: str) -> str:
    pattern = r"(?:预算(?:金额)?|最高限价|项目金额)[：:\s]*([0-9,.]+\s*(?:亿元|万元|万|元))"
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).replace(" ", "") if match else ""


def extract_deadline(text: str) -> str:
    pattern = r"(?:截止时间|投标截止|开标时间)[：:\s]*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)"
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def extract_owner(text: str) -> str:
    pattern = r"(?:采购人|招标人|建设单位)[：:\s]*([^，。；;\n]{2,60})"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""
