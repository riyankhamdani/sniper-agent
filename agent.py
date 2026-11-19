import html
import json
import os
import random
import re
import time
import xml.etree.ElementTree as ET
import requests
from google import genai
from google.genai import types

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
STATE_FILE = "seen_urls.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
}

# Role WAJIB di Judul / Paragraf Awal (Fokus ke Infra, Cloud, SRE, DevOps)
INFRA_ROLE_REGEX = re.compile(
    r"\b(devops|sre|site reliability|platform"
    r" engineer|infrastructure|infra|kubernetes|k8s|cloud engineer|cloud"
    r" architect|systems engineer|sysadmin|openshift|terraform|ansible|mlops)\b",
    re.IGNORECASE,
)

# Blacklist role non-teknis / non-infra
EXCLUDE_ROLES_REGEX = re.compile(
    r"\b(business development|sales|account"
    r" executive|marketing|recruiter|product"
    r" manager|frontend|react|mobile|ios|android|designer|ui/ux|commission-only|equity"
    r" only|unpaid)\b",
    re.IGNORECASE,
)

# Blacklist regional tertutup
GEO_EXCLUDE_REGEX = re.compile(
    r"(?:remote\s*[-–(:]\s*(?:us|usa|u\.s\.|uk|eu|europe|canada|india|latam|americas)\s*[)\],]|"
    r"\b(?:us|usa|uk|eu|canada|india)\s+only\b|"
    r"\b(?:us|usa|uk|eu)\s+citizens?\b|"
    r"\bw2\s+only\b|"
    r"\bsecurity\s+clearance\b|"
    r"\b(?:must\s+(?:be\s+located|reside)\s+in|applicants?\s+(?:must\s+be\s+)?based\s+in)\s+(?:the\s+)?(?:us|usa|uk|eu|europe|canada)\b)",
    re.IGNORECASE,
)

# Blacklist komitmen berat / tabrakan jam kerja full-time
HEAVY_COMMITMENT_REGEX = re.compile(
    r"(?:\b(?:35|40)\s*(?:hrs?|hours?)\s*(?:/|per)\s*week\b|"
    r"\bfull[\s-]?time\s+(?:hours|availability|commitment|schedule)\b|"
    r"\b(?:must|required\s+to)\s+overlap\s+with\s+(?:us|est|pst|cst|mst|cet|uk)\b|"
    r"\b(?:us|est|pst|cet)\s+business\s+hours\b)",
    re.IGNORECASE,
)

# Prioritas tinggi untuk side-gig async / fleksibel
ASYNC_PRIORITY_REGEX = re.compile(
    r"\b(async|asynchronous|part[\s-]?time|fractional|advisory|project[\s-]?based|retainer|bounty|flexible\s+hours|weekend)\b",
    re.IGNORECASE,
)

# Blacklist proyek yang mewajibkan modal langganan AI atau API key pribadi (BYOK)
BYOK_EXCLUDE_REGEX = re.compile(
    r"(?:\b(?:own|your|personal|bring\s+your\s+own)\s+(?:api\s+keys?|api\s+credits?|openai\s+key|anthropic\s+key|subscription|account)\b|"
    r"\bbyok\b|"
    r"\b(?:claude\s+pro|chatgpt\s+plus|gpt[\s-]?4\s+subscription|cursor\s+pro|midjourney\s+subscription)\s+(?:required|needed|must\s+have)\b|"
    r"\bmust\s+(?:have|provide|use)\s+(?:your\s+own\s+)?(?:claude\s+pro|chatgpt\s+plus|paid\s+ai|api\s+credits?)\b|"
    r"\bself[\s-]?funded\s+(?:api|tokens?|compute)\b)",
    re.IGNORECASE,
)

# Blacklist talent marketplace / agensi perantara / iklan rekrutmen massal
AGENCY_EXCLUDE_REGEX = re.compile(
    r"(?:\b(?:lemon\.io|toptal|turing\.com|bairesdev|andela|proxify|gun\.io|x-team|crossover|arc\.dev|usebraintrust)\b|"
    r"utm_medium=job_ad)",
    re.IGNORECASE,
)

# Skill mapping profil CV Riyan Khamdani
PROFILE_KEYWORDS = {
    "OpenShift / K8s": [
        "openshift",
        "kubernetes",
        "k8s",
        "gke",
        "eks",
        "aks",
        "podman",
        "docker",
    ],
    "IaC & Automation": ["terraform", "ansible", "ci/cd", "gitops", "argocd"],
    "Cloud & Linux": [
        "aws",
        "azure",
        "gcp",
        "google cloud",
        "alibaba cloud",
        "rhel",
        "linux",
    ],
    "Virtualization": ["vmware", "vsphere", "nsx-t", "zerto"],
    "AI Infra & MLOps": [
        "ai platform",
        "dataiku",
        "mlops",
        "gpu",
        "sre",
        "devops",
        "platform",
    ],
}

# Pola deteksi proyek lepas / kontrak / fractional / B2B / konsultan
CONTRACT_REGEX = re.compile(
    r"(?<!smart\s)(?<!federal\s)(?<!government\s)\b("
    r"contract|contractor|freelance|freelancer|b2b|c2c|corp[\s-]to[\s-]corp|"
    r"hourly|retainer|part[\s-]time|fractional|1099|project[\s-]based|"
    r"short[\s-]term|consultant|consulting|outside\s+ir35|independent\s+contractor"
    r")\b",
    re.IGNORECASE,
)


def load_seen_urls() -> set:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"[!] Gagal membaca {STATE_FILE}: {e}")
    return set()


def save_seen_urls(seen_urls: set):
    urls_to_save = list(seen_urls)[-1000:]
    with open(STATE_FILE, "w") as f:
        json.dump(urls_to_save, f, indent=2)


def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] TELEGRAM_BOT_TOKEN atau TELEGRAM_CHAT_ID belum diset.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID.strip(),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code != 200:
            print(f"[!] Gagal kirim HTML ({resp.text[:100]}), fallback teks biasa...")
            payload.pop("parse_mode")
            payload["text"] = re.sub(r"<[^>]+>", "", text)
            resp = requests.post(url, json=payload, timeout=15)
        return resp.status_code == 200
    except Exception as e:
        print(f"[!] Error Telegram API: {e}")
        return False


def extract_matched_skills(text: str) -> list:
    lower = text.lower()
    matched = []
    for category, kws in PROFILE_KEYWORDS.items():
        if any(re.search(r"\b" + re.escape(kw) + r"\b", lower) for kw in kws):
            matched.append(category)
    return matched


def is_valid_gig(
    header_or_title: str,
    full_text: str,
    is_freelance_board: bool = False,
    url: str = "",
) -> bool:
    combined_head = f"{header_or_title} {full_text[:1000]}"

    # Blokir iklan talent marketplace / agensi perantara
    if (
        AGENCY_EXCLUDE_REGEX.search(url)
        or AGENCY_EXCLUDE_REGEX.search(combined_head)
        or AGENCY_EXCLUDE_REGEX.search(full_text)
    ):
        return False

    # Tolak full-time kecuali ada penegasan kontrak / freelance / part-time / async
    if re.search(
        r"\b(full[\s-]time|permanent\s+role|w2\s+employee)\b",
        combined_head,
        re.IGNORECASE,
    ):
        if not (
            ASYNC_PRIORITY_REGEX.search(combined_head)
            or CONTRACT_REGEX.search(combined_head)
        ):
            return False

    # Tolak kontrak yang menuntut 35-40 jam/minggu atau wajib overlap jam kerja Barat
    if HEAVY_COMMITMENT_REGEX.search(combined_head) or HEAVY_COMMITMENT_REGEX.search(
        full_text
    ):
        return False

    if GEO_EXCLUDE_REGEX.search(combined_head) or GEO_EXCLUDE_REGEX.search(full_text):
        return False

    if BYOK_EXCLUDE_REGEX.search(combined_head) or BYOK_EXCLUDE_REGEX.search(full_text):
        return False

    if EXCLUDE_ROLES_REGEX.search(header_or_title):
        return False

    if not INFRA_ROLE_REGEX.search(combined_head):
        return False

    if not is_freelance_board:
        return bool(
            CONTRACT_REGEX.search(combined_head) or CONTRACT_REGEX.search(full_text)
        )

    return True


def fetch_reddit_hiring_gigs() -> list:
    """Scout Utama 1: Postingan [Hiring] dari Reddit (OAuth API -> Fallback RSS per subreddit)."""
    jobs = []
    subreddits = ["forhire", "freelance_forhire", "devopsjobs"]
    user_agent = "python:sidegig-infra-radar:v1.2 (by /u/riyan_infra_bot)"

    client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()

    # JALUR 1: Reddit Official OAuth (kebal blokir IP cloud / GitHub Actions)
    if client_id and client_secret:
        try:
            auth_resp = requests.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": user_agent},
                timeout=15,
            )
            auth_resp.raise_for_status()
            token = auth_resp.json().get("access_token")

            oauth_headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": user_agent,
            }
            url = f"https://oauth.reddit.com/r/{'+'.join(subreddits)}/new?limit=75"
            resp = requests.get(url, headers=oauth_headers, timeout=15)
            resp.raise_for_status()

            for child in resp.json().get("data", {}).get("children", []):
                post = child.get("data", {})
                title = str(post.get("title") or "").strip()
                flair = str(post.get("link_flair_text") or "").lower()
                body = str(post.get("selftext") or "").strip()

                if "[hiring]" not in title.lower() and "hiring" not in flair:
                    continue
                if "[for hire]" in title.lower():
                    continue

                item_url = f"https://www.reddit.com{post.get('permalink', '')}"
                subreddit = post.get("subreddit", "")
                is_freelance_sub = subreddit.lower() in ["forhire", "freelance_forhire"]

                if is_valid_gig(
                    title, body, is_freelance_board=is_freelance_sub, url=item_url
                ):
                    matched = extract_matched_skills(f"{title} {body}")
                    if matched:
                        jobs.append({
                            "source": f"Reddit r/{subreddit}",
                            "title": title,
                            "company": f"u/{post.get('author', 'client')}",
                            "url": item_url,
                            "date": time.strftime(
                                "%Y-%m-%d", time.gmtime(post.get("created_utc", 0))
                            ),
                            "matches": matched,
                            "description": body[:1500] or title,
                        })

            print(f"[*] Reddit [Hiring] (OAuth) valid: {len(jobs)} listing")
            return jobs
        except Exception as e:
            print(f"[!] Reddit OAuth gagal ({e}), beralih ke mode RSS...")

    # JALUR 2: Fallback RSS per subreddit (tanpa kredensial API)
    rss_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) "
            "Gecko/20100101 Firefox/132.0"
        ),
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9",
    }
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for sub in subreddits:
        try:
            rss_url = f"https://www.reddit.com/r/{sub}/new/.rss?limit=40"
            resp = requests.get(rss_url, headers=rss_headers, timeout=15)
            resp.raise_for_status()

            root = ET.fromstring(resp.text)
            is_freelance_sub = sub.lower() in ["forhire", "freelance_forhire"]

            for entry in root.findall("atom:entry", ns):
                title_el = entry.find("atom:title", ns)
                link_el = entry.find("atom:link", ns)
                content_el = entry.find("atom:content", ns)
                author_el = entry.find("atom:author/atom:name", ns)
                updated_el = entry.find("atom:updated", ns)

                title = (
                    html.unescape(title_el.text or "").strip()
                    if title_el is not None
                    else ""
                )
                if "[hiring]" not in title.lower() or "[for hire]" in title.lower():
                    continue

                item_url = (
                    link_el.attrib.get("href", "").strip()
                    if link_el is not None
                    else ""
                )
                raw_html = (
                    html.unescape(content_el.text or "")
                    if content_el is not None
                    else ""
                )
                body = html.unescape(re.sub(r"<[^>]+>", " ", raw_html)).strip()
                author = (
                    (author_el.text or "client").replace("/u/", "").strip()
                    if author_el is not None
                    else "client"
                )
                date_str = (
                    (updated_el.text or "")[:10] if updated_el is not None else ""
                )

                if is_valid_gig(
                    title, body, is_freelance_board=is_freelance_sub, url=item_url
                ):
                    matched = extract_matched_skills(f"{title} {body}")
                    if matched:
                        jobs.append({
                            "source": f"Reddit r/{sub}",
                            "title": title,
                            "company": f"u/{author}",
                            "url": item_url,
                            "date": date_str,
                            "matches": matched,
                            "description": body[:1500] or title,
                        })

            time.sleep(1.5)
        except Exception as e:
            print(f"[-] Reddit RSS (r/{sub}) error: {e}")

    print(f"[*] Reddit [Hiring] (RSS) valid: {len(jobs)} listing")
    return jobs


def fetch_himalayas_contracts() -> list:
    """Scout Utama 2: Proyek Part-Time / Contractor / Freelance dari Himalayas API."""
    jobs = []
    try:
        url = "https://himalayas.app/jobs/api?limit=50"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        res = resp.json()

        for item in res.get("jobs", []):
            emp_type = str(item.get("employmentType") or "").lower()
            if not any(
                k in emp_type for k in ["part", "contract", "freelance", "temp"]
            ):
                continue

            restrictions = item.get("locationRestrictions") or []
            if restrictions and not any(
                any(
                    ok in str(loc).lower()
                    for ok in ["worldwide", "anywhere", "asia", "apac", "indonesia"]
                )
                for loc in restrictions
            ):
                continue

            title = str(item.get("title") or "").strip()
            desc = re.sub(
                r"<[^>]+>",
                " ",
                str(item.get("description") or item.get("excerpt") or ""),
            )
            item_url = str(item.get("applicationLink") or item.get("guid") or "")
            header_meta = f"{title} | {emp_type}"

            if is_valid_gig(
                header_meta, desc, is_freelance_board=True, url=item_url
            ):
                matched = extract_matched_skills(f"{title} {desc}")
                if matched:
                    jobs.append({
                        "source": f"Himalayas ({item.get('employmentType')})",
                        "title": title,
                        "company": str(item.get("companyName") or "Client").strip(),
                        "url": item_url,
                        "date": str(item.get("pubDate") or "")[:10],
                        "matches": matched,
                        "description": desc[:1500],
                    })
    except Exception as e:
        print(f"[-] Himalayas error: {e}")

    print(f"[*] Himalayas kontrak/part-time valid: {len(jobs)} listing")
    return jobs


def fetch_hn_freelance_gigs() -> list:
    """Scout 3: Scan thread 'Seeking Freelancer' & kontrak di Hacker News."""
    jobs = []
    try:
        url = "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=12"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        hits = resp.json().get("hits", [])

        target_stories = [
            h
            for h in hits
            if "freelancer" in h.get("title", "").lower()
            or "hiring" in h.get("title", "").lower()
        ]

        for story in target_stories[:4]:
            story_id = story["objectID"]
            story_title = story.get("title", "").lower()
            is_freelancer_thread = "freelancer" in story_title

            query_str = (
                "devops OR kubernetes OR terraform OR sre OR infrastructure OR aws OR"
                " azure OR cloud OR openshift"
            )
            comments_url = (
                "https://hn.algolia.com/api/v1/search_by_date"
                f"?tags=comment,story_{story_id}"
                f"&query={requests.utils.quote(query_str)}"
                f"&hitsPerPage=500"
            )
            c_resp = requests.get(comments_url, headers=HEADERS, timeout=15)
            c_resp.raise_for_status()

            for hit in c_resp.json().get("hits", []):
                if str(hit.get("parent_id")) != str(story_id):
                    continue

                raw_html = hit.get("comment_text", "")
                text = html.unescape(raw_html)
                text_with_newlines = re.sub(r"</?p>", "\n", text)
                clean_text = re.sub(r"<[^>]+>", " ", text_with_newlines).strip()
                upper = clean_text.upper()

                if "SEEKING WORK" in upper or "FOR HIRE" in upper:
                    continue

                lines = [
                    line.strip() for line in clean_text.splitlines() if line.strip()
                ]
                first_line = lines[0] if lines else clean_text[:160]

                if "ONSITE" in first_line.upper() and "REMOTE" not in first_line.upper():
                    continue

                item_url = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                if is_valid_gig(
                    first_line,
                    clean_text,
                    is_freelance_board=is_freelancer_thread,
                    url=item_url,
                ):
                    matched_skills = extract_matched_skills(clean_text)
                    if matched_skills:
                        snippet = clean_text[:140].replace("\n", " ") + "..."
                        tag_label = (
                            "HN Seeking Freelancer"
                            if is_freelancer_thread
                            else "HN Contract"
                        )
                        jobs.append({
                            "source": tag_label,
                            "title": snippet,
                            "company": hit.get("author", "HN Client"),
                            "url": item_url,
                            "date": hit.get("created_at", "")[:10],
                            "matches": matched_skills,
                            "description": clean_text[:1500],
                        })
    except Exception as e:
        print(f"[-] HN error: {e}")

    print(f"[*] HN freelance/kontrak valid: {len(jobs)} listing")
    return jobs


def fetch_remotive_contracts() -> list:
    """Scout 4: Proyek kontrak/B2B DevOps & Software dari Remotive."""
    jobs = []
    categories = ["devops", "software-dev"]
    seen_ids = set()

    for cat in categories:
        try:
            url = f"https://remotive.com/api/remote-jobs?category={cat}&limit=60"
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()

            for item in resp.json().get("jobs", []):
                job_id = item.get("id")
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                job_type = str(item.get("job_type") or "").lower()
                title = str(item.get("title") or "").strip()
                desc = re.sub(r"<[^>]+>", " ", str(item.get("description") or ""))
                geo = str(item.get("candidate_required_location") or "").lower()
                item_url = str(item.get("url") or "")

                is_explicit_contract_type = job_type in [
                    "contract",
                    "freelance",
                    "part_time",
                ]

                if geo and not any(
                    term in geo
                    for term in [
                        "worldwide",
                        "anywhere",
                        "global",
                        "apac",
                        "asia",
                        "indonesia",
                        "utc",
                        "gmt",
                    ]
                ):
                    continue

                header_meta = f"{title} | {geo} | {job_type}"
                if is_valid_gig(
                    header_meta,
                    desc,
                    is_freelance_board=is_explicit_contract_type,
                    url=item_url,
                ):
                    matched = extract_matched_skills(f"{title} {desc}")
                    if matched:
                        jobs.append({
                            "source": f"Remotive ({job_type.title() or 'Contract'})",
                            "title": title,
                            "company": str(item.get("company_name") or "Client").strip(),
                            "url": item_url,
                            "date": str(item.get("publication_date") or "")[:10],
                            "matches": matched,
                            "description": desc[:1500],
                        })
        except Exception as e:
            print(f"[-] Remotive ({cat}) error: {e}")

    print(f"[*] Remotive kontrak/freelance valid: {len(jobs)} listing")
    return jobs


def fetch_remoteok_contracts() -> list:
    """Scout 5: Listing kontrak/freelance dari feed utama RemoteOK API."""
    jobs = []
    try:
        url = "https://remoteok.com/api"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        res = resp.json()
        listings = res[1:] if isinstance(res, list) and len(res) > 1 else []

        for item in listings[:100]:
            title = str(item.get("position") or "").strip()
            tags = " ".join(str(t) for t in item.get("tags", [])).lower()
            desc = re.sub(r"<[^>]+>", " ", str(item.get("description") or ""))
            location = str(item.get("location") or "").lower()
            item_url = str(
                item.get("url")
                or f"https://remoteok.com/remote-jobs/{item.get('id')}"
            )

            if location and not any(
                term in location
                for term in [
                    "worldwide",
                    "anywhere",
                    "global",
                    "remote",
                    "apac",
                    "asia",
                ]
            ):
                continue

            header_meta = f"{title} | {location} | {tags}"
            if is_valid_gig(
                header_meta, desc, is_freelance_board=False, url=item_url
            ):
                matched = extract_matched_skills(f"{title} {tags} {desc}")
                if matched:
                    jobs.append({
                        "source": "RemoteOK (Contract)",
                        "title": title,
                        "company": str(item.get("company") or "Client").strip(),
                        "url": item_url,
                        "date": str(item.get("date") or "")[:10],
                        "matches": matched,
                        "description": desc[:1500],
                    })
    except Exception as e:
        print(f"[-] RemoteOK error: {e}")

    print(f"[*] RemoteOK kontrak/freelance valid: {len(jobs)} listing")
    return jobs


def fetch_wwr_contracts() -> list:
    """Scout 6: Kontrak DevOps & Programming dari RSS WeWorkRemotely."""
    jobs = []
    feeds = [
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    ]
    seen_links = set()

    for url in feeds:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            res.raise_for_status()
            items = re.findall(r"<item>(.*?)</item>", res.text, re.DOTALL)

            for raw_item in items[:40]:
                title_m = re.search(
                    r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                    raw_item,
                    re.DOTALL,
                )
                link_m = re.search(r"<link>(.*?)</link>", raw_item)
                desc_m = re.search(
                    r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>",
                    raw_item,
                    re.DOTALL,
                )
                region_m = re.search(r"<region>(.*?)</region>", raw_item)

                title = html.unescape(title_m.group(1)).strip() if title_m else ""
                link = link_m.group(1).strip() if link_m else ""
                if not link or link in seen_links:
                    continue
                seen_links.add(link)

                desc = (
                    re.sub(r"<[^>]+>", " ", html.unescape(desc_m.group(1)))
                    if desc_m
                    else ""
                )
                region = region_m.group(1).strip().lower() if region_m else ""

                header_meta = f"{title} | {region}"
                if is_valid_gig(
                    header_meta, desc, is_freelance_board=False, url=link
                ):
                    matched = extract_matched_skills(f"{title} {desc}")
                    if matched:
                        jobs.append({
                            "source": "WeWorkRemotely (Contract)",
                            "title": title,
                            "company": (
                                title.split(":")[0].strip() if ":" in title else "WWR Client"
                            ),
                            "url": link,
                            "date": "",
                            "matches": matched,
                            "description": desc[:1500],
                        })
        except Exception as e:
            print(f"[-] WWR error: {e}")

    print(f"[*] WeWorkRemotely kontrak valid: {len(jobs)} listing")
    return jobs


def fetch_jobicy_contracts() -> list:
    """Scout 7: Listing kontrak/freelance DevOps dari Jobicy API."""
    jobs = []
    try:
        url = "https://jobicy.com/api/v2/remote-jobs?count=50&tag=devops"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        for item in resp.json().get("jobs", []):
            title = str(item.get("jobTitle") or "").strip()
            job_type = (
                " ".join(item.get("jobType", [])).lower()
                if isinstance(item.get("jobType"), list)
                else str(item.get("jobType") or "").lower()
            )
            geo = str(item.get("jobGeo") or "").lower()
            desc = re.sub(
                r"<[^>]+>",
                " ",
                str(item.get("jobDescription") or item.get("jobExcerpt") or ""),
            )
            item_url = str(item.get("url") or "")

            if geo and not any(
                term in geo
                for term in ["anywhere", "worldwide", "global", "apac", "asia"]
            ):
                continue

            is_contract_type = any(
                t in job_type for t in ["contract", "freelance", "part-time"]
            )
            header_meta = f"{title} | {geo} | {job_type}"
            if is_valid_gig(
                header_meta,
                desc,
                is_freelance_board=is_contract_type,
                url=item_url,
            ):
                matched = extract_matched_skills(f"{title} {desc}")
                if matched:
                    jobs.append({
                        "source": "Jobicy (Contract)",
                        "title": title,
                        "company": str(item.get("companyName") or "Client").strip(),
                        "url": item_url,
                        "date": str(item.get("pubDate") or "")[:10],
                        "matches": matched,
                        "description": desc[:1500],
                    })
    except Exception as e:
        print(f"[-] Jobicy error: {e}")

    print(f"[*] Jobicy kontrak/freelance valid: {len(jobs)} listing")
    return jobs


def fetch_workingnomads_contracts() -> list:
    """Scout 8: Listing kontrak DevOps dari Working Nomads API."""
    jobs = []
    try:
        url = "https://www.workingnomads.com/api/exposed_jobs/"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()

        for item in resp.json()[:150]:
            cat = str(item.get("category_name") or "").lower()
            if "devops" not in cat and "system" not in cat and "development" not in cat:
                continue

            title = str(item.get("title") or "").strip()
            tags = str(item.get("tags") or "").lower()
            location = str(item.get("location") or "").lower()
            desc = re.sub(r"<[^>]+>", " ", str(item.get("description") or ""))
            item_url = str(item.get("url") or "")

            if location and not any(
                term in location
                for term in ["anywhere", "worldwide", "global", "apac", "asia", "remote"]
            ):
                continue

            header_meta = f"{title} | {location} | {tags}"
            if is_valid_gig(
                header_meta, desc, is_freelance_board=False, url=item_url
            ):
                matched = extract_matched_skills(f"{title} {tags} {desc}")
                if matched:
                    jobs.append({
                        "source": "WorkingNomads (Contract)",
                        "title": title,
                        "company": str(item.get("company_name") or "Client").strip(),
                        "url": item_url,
                        "date": str(item.get("pub_date") or "")[:10],
                        "matches": matched,
                        "description": desc[:1500],
                    })
    except Exception as e:
        print(f"[-] WorkingNomads error: {e}")

    print(f"[*] WorkingNomads kontrak valid: {len(jobs)} listing")
    return jobs


def generate_with_fallback(
    client,
    prompt: str,
    temperature: float = 0.3,
    retries_per_model: int = 3,
    initial_delay: int = 4,
) -> str:
    """Memanggil Gemini API dengan prioritas model aktif terbaru."""
    preferred = [
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]
    candidate_models = list(preferred)

    try:
        active_models = [m.name.replace("models/", "") for m in client.models.list()]
        for m in active_models:
            if "gemini" in m and "embedding" not in m and m not in candidate_models:
                candidate_models.append(m)
    except Exception:
        pass

    config = types.GenerateContentConfig(
        temperature=temperature,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    for model_name in candidate_models:
        for attempt in range(1, retries_per_model + 1):
            try:
                response = client.models.generate_content(
                    model=model_name, contents=prompt, config=config
                )
                if response.text:
                    return response.text.strip()
            except Exception as e:
                err_msg = str(e)
                if any(
                    k in err_msg.lower()
                    for k in ["not found", "404", "deprecated", "limit: 0", "unsupported"]
                ):
                    break
                time.sleep((initial_delay * attempt) + random.uniform(1.0, 2.5))

    raise Exception("Seluruh kandidat model Gemini gagal merespons.")


def agent_solutions_architect(client, job: dict) -> str:
    """Karyawan 2 (Solutions Architect): Meracik pitch async / side-gig B2B tanpa ganggu full-time."""
    prompt = f"""
You are Riyan Khamdani, a Senior Cloud & DevOps/SRE Engineer (experienced in Kubernetes, OpenShift, Terraform, AWS/Azure/GCP, CI/CD, and AI infra).
Write a sharp, direct B2B async/fractional freelance pitch for this opportunity:

Source: {job['source']}
Client/Company: {job['company']}
Title: {job['title']}
Matched Skills: {', '.join(job['matches'])}
Details:
{job.get('description', job['title'])}

Rules:
1. NO robotic AI fluff (strictly avoid "Dear hiring manager", "I hope this finds you well", "I am excited to apply", "thrilled").
2. Keep it under 95 words in natural, confident English.
3. Explicitly frame your availability as an **async, milestone-based, or fractional specialist (10–15 hrs/week)** so expectations are clear from day one.
4. Include 3 concrete technical "micro-audit / execution" bullets tailored specifically to their stack or pain points mentioned in the details.
5. End with a low-friction async call to action (e.g., offering a quick async architecture audit or brief sync).
6. Output ONLY the pitch text, nothing else.
"""
    return generate_with_fallback(client, prompt, temperature=0.4)


def agent_quality_control(client, job: dict, pitch: str) -> dict:
    """Karyawan 3 (QC Gatekeeper): Menilai kelayakan side-gig agar tidak mengganggu full-time."""
    prompt = f"""
You are a strict QC Manager for a Senior Cloud/DevOps Side-Gig Sniper pipeline.
The candidate currently holds a daytime full-time Cloud Engineer role (GMT+7 / Jakarta) and ONLY wants safe, non-conflicting side gigs with direct clients (NOT talent agencies/marketplaces).

Evaluate this gig and the drafted pitch:
Title: {job['title']}
Source: {job['source']}
URL: {job['url']}
Details: {job.get('description', '')[:800]}
Drafted Pitch:
{pitch}

Scoring Criteria (1-10):
- Give 8-10 ONLY IF:
  1) It is a direct client side-gig (part-time, fractional, project-based, bounty, or async contract <= 15-20 hrs/week) WITHOUT interfering with a daytime full-time job.
  2) Globally remote-friendly (open to APAC/Indonesia, no mandatory 9-to-5 US/EU synchronous meetings).
  3) Does NOT require the freelancer to pay out-of-pocket for AI subscriptions/API keys (no BYOK).
  4) Strongly matches Cloud/K8s/Terraform/SRE/AI-infra skills, and the pitch is specific and human.
- Give 1-7 IF:
  1) It is a disguised full-time role (35-40 hrs/week), a talent agency/marketplace ad, or demands rigid synchronous overlap during daytime/late-night GMT+7.
  2) Has hidden country restrictions (US/EU/UK only) or requires BYOK paid subscriptions.
  3) Vague/low-value or generic pitch.

Output STRICTLY valid JSON without markdown blocks:
{{"score": 8, "reason": "Alasan singkat dalam bahasa Indonesia (sebutkan estimasi fleksibilitas waktunya)", "approved": true}}
"""
    try:
        raw_res = generate_with_fallback(client, prompt, temperature=0.1)
        clean_json = raw_res.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        print(f"[!] Warning QC parse gagal ({e}), fallback skor 7.")
        return {"score": 7, "reason": "Fallback QC parse", "approved": False}


def main():
    seen_urls = load_seen_urls()
    print(f"[*] Total history seen URLs: {len(seen_urls)}")

    all_jobs = []
    # Sumber prioritas tinggi khusus freelance & kontrak pendek
    all_jobs.extend(fetch_reddit_hiring_gigs())
    all_jobs.extend(fetch_himalayas_contracts())
    # Sumber sekunder (HN bulanan & job boards)
    all_jobs.extend(fetch_hn_freelance_gigs())
    all_jobs.extend(fetch_remotive_contracts())
    all_jobs.extend(fetch_remoteok_contracts())
    all_jobs.extend(fetch_wwr_contracts())
    all_jobs.extend(fetch_jobicy_contracts())
    all_jobs.extend(fetch_workingnomads_contracts())

    # Deduplikasi URL lintas sumber
    unique_new_jobs = []
    batch_urls = set()
    for job in all_jobs:
        u = job.get("url", "")
        if u and u not in seen_urls and u not in batch_urls:
            batch_urls.add(u)
            unique_new_jobs.append(job)

    # Prioritaskan proyek yang eksplisit menyebut async / part-time / project-based / advisory
    unique_new_jobs.sort(
        key=lambda j: bool(
            ASYNC_PRIORITY_REGEX.search(f"{j['title']} {j.get('description', '')}")
        ),
        reverse=True,
    )

    print(
        f"[*] Total lolos kurasi awal: {len(all_jobs)}, listing baru: {len(unique_new_jobs)}"
    )

    if not unique_new_jobs:
        print("[!] Tidak ada proyek side-gig aman hari ini. Bot standby.")
        return

    candidates = unique_new_jobs[:5]

    if not GEMINI_API_KEY:
        print("[!] GEMINI_API_KEY belum diset. Mengirim mode radar standar...")
        message = "<b>⚡ SIDE-GIG SNIPER RADAR (NO-AI FALLBACK)</b>\n====================================\n\n"
        for idx, job in enumerate(candidates, 1):
            message += f"<b>{idx}. {html.escape(job['title'])}</b>\n🔗 <a href='{job['url']}'>Apply</a>\n\n"
        if send_telegram_message(message):
            for job in candidates:
                seen_urls.add(job["url"])
            save_seen_urls(seen_urls)
        return

    client = genai.Client(api_key=GEMINI_API_KEY)
    approved_gigs = []
    evaluated_urls = set()

    for job in candidates:
        print(f"[*] AI memproses: {job['title'][:60]}...")
        try:
            pitch = agent_solutions_architect(client, job)
            qc = agent_quality_control(client, job, pitch)
            score = qc.get("score", 0)
            reason = qc.get("reason", "-")
            print(f"    -> QC Skor: {score}/10 | {reason}")
            evaluated_urls.add(job["url"])

            if score >= 8 and qc.get("approved", False):
                job["ai_pitch"] = pitch
                job["ai_score"] = score
                job["qc_reason"] = reason
                approved_gigs.append(job)

            time.sleep(2)
        except Exception as e:
            print(f"[-] Gagal memproses kandidat via AI: {e}")

    if not approved_gigs:
        seen_urls.update(evaluated_urls)
        save_seen_urls(seen_urls)
        print("🛑 Tidak ada prospek side-gig yang lolos QC (skor >= 8) hari ini. Telegram tidak di-spam.")
        return

    message = (
        "<b>🎯 ASYNC SIDE-GIG BRIEFING (QC APPROVED)</b>\n"
        "<i>Aman untuk full-time • Skor ≥ 8 • Pitch siap copas</i>\n"
        "====================================\n\n"
    )

    for idx, job in enumerate(approved_gigs, 1):
        tags_formatted = " | ".join(job["matches"])
        message += (
            f"<b>{idx}. [{job['ai_score']}/10] {html.escape(job['title'])}</b>\n"
            f"🏢 <b>Client:</b> {html.escape(job['company'])} | <b>Via:</b> {html.escape(job['source'])}\n"
            f"🎯 <b>Stack:</b> <code>{html.escape(tags_formatted)}</code>\n"
            f"🛡️ <b>QC Note:</b> <i>{html.escape(job['qc_reason'])}</i>\n\n"
            f"📋 <b>Async Pitch Siap Copas:</b>\n"
            f"<blockquote>{html.escape(job['ai_pitch'])}</blockquote>\n"
            f"🔗 <a href='{job['url']}'>Direct Apply / Contact</a>\n\n"
        )

    if send_telegram_message(message):
        seen_urls.update(evaluated_urls)
        save_seen_urls(seen_urls)
        print(f"[✓] Berhasil mengirim {len(approved_gigs)} prospek side-gig ke Telegram.")
    else:
        print("[!] Gagal mengirim ke Telegram; seen_urls.json tidak diperbarui agar dicoba ulang.")


if __name__ == "__main__":
    main()
