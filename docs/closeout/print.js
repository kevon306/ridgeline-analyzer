// Prints closeout.html to PDF: node docs/closeout/print.js <in.html> <out.pdf>
const { chromium } = require('playwright');
(async () => {
  const [src, out] = process.argv.slice(2);
  const b = await chromium.launch(); const p = await b.newPage();
  await p.goto('file://' + require('path').resolve(src)); await p.waitForTimeout(500);
  await p.pdf({ path: out, format: 'Letter', printBackground: true, displayHeaderFooter: true,
    margin: { top: '0.4in', bottom: '0.5in', left: '0.55in', right: '0.55in' }, headerTemplate: '<span></span>',
    footerTemplate: '<div style="font:7px Liberation Sans,Arial;color:#677082;width:100%;padding:0 0.55in;display:flex;justify-content:space-between"><span>Ridgeline Property Analytics · Project Closeout · v1.0</span><span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>' });
  await b.close();
})();
