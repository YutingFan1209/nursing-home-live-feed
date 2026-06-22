from playwright.sync_api import sync_playwright

def search_ny_ucc(owner_name: str):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto("https://ucc-efiling.dos.ny.gov/OnlineUCCSearch/OnlineUCCSearch")
        page.wait_for_timeout(2000)

        # 选Debtor Name
        page.click("input[value='DebtorName']")
        page.wait_for_timeout(500)

        # 选Organization
        page.click("#rdbOrg")
        page.wait_for_timeout(1000)

        # 找Organization Name输入框 - 用placeholder或label
        org_input = page.locator("input[name*='OrgName'], input[id*='OrgName'], input[placeholder*='Organization']").first
        org_input.fill(owner_name)
        page.wait_for_timeout(500)

        # 点Search
        page.locator("button:has-text('Search'), input[value='Search'], #btnSearch").first.click()
        page.wait_for_timeout(5000)

        html = page.content()
        browser.close()
        return html

html = search_ny_ucc("Ensign")
idx = html.find("Lien Number")
print(html[idx:idx+3000] if idx > 0 else "No results found in HTML")
