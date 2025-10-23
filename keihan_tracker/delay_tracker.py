from pydantic import BaseModel
from bs4 import BeautifulSoup as bs
import asyncio
import httpx
from urllib.parse import urljoin

BASE_URL = "https://transit.yahoo.co.jp/diainfo/area/"

class DelayLine(BaseModel):
    LineName: str
    status: str
    detail: str

async def get_yahoo_delay(area:int=6) -> list[DelayLine]:
    async with httpx.AsyncClient() as crowler:
        html = await crowler.get(f"{BASE_URL}{area}")

        soup = bs(html.content, "html.parser")
        
        table = soup.select("div.elmTblLstLine.trouble")[0].find_all("table")
        table = table[0] if len(table) != 0 else None
        if not table:
            return []
        
        async def get_detail(url:str) -> tuple[str,str]:
            res = await crowler.get(url,follow_redirects=True)
            soup = bs(res.text, "html.parser")
            info = soup.select("div#mdServiceStatus")[0]
            title = info.select("dl > dt")[0].text
            text = info.select("dl > dd > p")[0].text

            return title, text

        delays:list[DelayLine] = []
        tasks:list = []
        lines:list[tuple[str,str]] = []
        for tr in table.find_all("tr")[1:]:
            url = urljoin(BASE_URL, str(tr.find_all("td")[0].find_all("a")[0].get("href")))
            line = tr.find_all("td")[0].text    # ○○線
            short_status = tr.find_all("td")[2].text  # 17:00頃、宇都宮...
            tasks.append(get_detail(url))
            lines.append((line, short_status))

        for i, line in zip(await asyncio.gather(*tasks), lines):
            line, status = line
            title, text = i
            delays.append(DelayLine(LineName=line, status=title, detail=text))
        return delays