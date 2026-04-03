import { chromium } from "playwright";

test("app loads homepage", async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto("http://localhost:3000");
  await browser.close();
});
