#!/usr/bin/env python3
import os
import re
import yt_dlp.extractor as ie

# Mainstream or false-positive platforms that must NEVER be in adult_extractors
MAINSTREAM_WHITELIST = {
    "adultswim",
    "bilibili",
    "bluesky",
    "cda",
    "dailymotion",
    "essex",
    "facebook",
    "generic",
    "goodgame",
    "instagram",
    "jiosaavn",
    "kick",
    "lasexta",
    "linkedin",
    "mave",
    "middlesex",
    "mxplayer",
    "newgrounds",
    "niconico",
    "niconicochannelplus",
    "ondemandkorea",
    "peertube",
    "picarto",
    "pinterest",
    "pr0gramm",
    "reddit",
    "scrolller",
    "sexta",
    "snapchat",
    "soundcloud",
    "sussex",
    "tiktok",
    "twitch",
    "twitter",
    "vevo",
    "vimeo",
    "yandexvideo",
    "youtube",
}

# Strict adult keywords that indicate adult tube, cam, or hentai services
STRICT_ADULT_KEYWORDS = [
    r"porn", r"xxx", r"hentai", r"cam4", r"camsoda", r"chaturbate",
    r"bongacams", r"stripchat", r"eporner", r"redtube", r"youporn",
    r"xvideos", r"xnxx", r"xhamster", r"spankbang", r"beeg", r"tube8",
    r"brazzers", r"motherless", r"tnaflix", r"rule34", r"nuvid",
    r"drtuber", r"slutload", r"peekvids", r"manyvids", r"zenporn",
    r"alphaporno", r"behindkink", r"erocast", r"eroprofile", r"hellporno",
    r"lovehomeporn", r"mochavideo", r"moviefap", r"murrtube", r"nonktube",
    r"noodlemagazine", r"nubilesporn", r"pornbox", r"pornerbros", r"pornflip",
    r"pornhub", r"pornotube", r"pornovoisines", r"pornox", r"porntop",
    r"porntube", r"sauceplus", r"sexu", r"thisvid", r"toypics", r"txxx",
    r"youjizz", r"playvids", r"redgifs", r"sunporno", r"xtube", r"fux",
    r"goshgay", r"empflix", r"xstream", r"4tube"
]

def generate_adult_blocklist():
    extractors = ie.gen_extractors()
    candidates = set()
    keyword_regex = re.compile("|".join(STRICT_ADULT_KEYWORDS), re.IGNORECASE)

    for ex in extractors:
        ie_name = ex.IE_NAME
        base_name = ie_name.split(":")[0].lower()

        if base_name in MAINSTREAM_WHITELIST or ie_name.lower() in MAINSTREAM_WHITELIST:
            continue

        # Check IE_NAME against strict keywords
        if keyword_regex.search(ie_name):
            candidates.add(base_name)
            continue

        # Check domain pattern in _VALID_URL
        valid_url = getattr(ex, "_VALID_URL", "")
        if isinstance(valid_url, str):
            # Extract domain portion roughly
            match = re.search(r"https?://(?:\(\?:)?(?:www\.)?([^/:\)\?]+)", valid_url)
            if match:
                domain_part = match.group(1).lower()
                if keyword_regex.search(domain_part):
                    candidates.add(base_name)

    return sorted(candidates)

if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    blocked = generate_adult_blocklist()
    out_file = "data/adult_extractors.txt"

    with open(out_file, "w") as f:
        f.write("# Reviewed list of adult extractors to reject when ALLOW_ADULT=false\n")
        f.write("# Whitelisted: AdultSwim, YouTube, Reddit, Twitter, etc.\n")
        for name in blocked:
            f.write(f"{name}\n")

    print(f"Generated and reviewed {len(blocked)} adult extractor candidates in {out_file}:")
    for name in blocked:
        print(f"  - {name}")
