from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://www.njportal.com/ucc/search/noncertifiedsearch.aspx")
    page.wait_for_timeout(2000)
    page.click("#ctl00_mainContent_DebtorSearch1_Wizard1_radioSwitchOrgPerson_1")
    page.wait_for_timeout(500)
    page.click("#ctl00_mainContent_DebtorSearch1_Wizard1_radioOutputList_0")
    page.wait_for_timeout(500)
    page.click("input[value='Continue'], button:has-text('Continue')")
    page.wait_for_timeout(2000)
    page.check("input[type='checkbox']")
    page.fill("#ctl00_mainContent_DebtorSearch1_Wizard1_txtOrganizationName", "COMPLETE CARE")
    page.click("input[value='Search'], button:has-text('Search')")
    page.wait_for_timeout(4000)
    soup = BeautifulSoup(page.content(), "html.parser")
    tables = soup.find_all("table")
    print(f"Tables found: {len(tables)}")
    for t in tables:
        headers = [th.get_text(strip=True) for th in t.find_all("th")]
        if "Filing Number" in headers or "Organization" in headers:
            print("Headers:", headers)
            rows = t.select("tbody tr")
            for row in rows[:3]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                print(cells)
            break
    browser.close()
