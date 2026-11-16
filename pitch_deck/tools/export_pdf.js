// Export the deck to PDF (one 1920x1080 page per slide) using the preinstalled Playwright.
// Usage: node pitch_deck/tools/export_pdf.js [out.pdf]
const path = require('path');
const { chromium } = require('playwright');
(async () => {
  const deck = path.resolve(__dirname, '..', 'index.html');
  const out = path.resolve(process.argv[2] || path.join(__dirname, '..', 'sepazu_pitch.pdf'));
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
  await p.goto('file://' + deck);
  await p.waitForTimeout(1500); // fonts + images
  // Count-up numbers start at 0 on screen; print the final values.
  await p.evaluate(() => document.querySelectorAll('[data-count]').forEach(e => e.textContent = (+e.dataset.count).toFixed(+e.dataset.dec || 0)));
  await p.emulateMedia({ media: 'print' });
  await p.pdf({ path: out, width: '1920px', height: '1080px', printBackground: true, preferCSSPageSize: true });
  await b.close();
  console.log('wrote', out);
})();
