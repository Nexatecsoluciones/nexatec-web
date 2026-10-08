// E2E del sitio publico (ver docs/E2E.md): home, pedido de demo real,
// paginas legales, aviso de cookies y movil. El pedido de prueba usa un
// email e2e-... que despues se borra con scripts/e2e_tenant.py.
// Uso: node public-site.e2e.mjs <email-de-prueba>
import { chromium } from "playwright";
import fs from "node:fs";

const EMAIL = process.argv[2];
const BASE = "https://staging.nexatecpy.com";
const SHOTS = process.env.E2E_SHOTS ?? new URL("./shots/", import.meta.url).pathname;
fs.mkdirSync(SHOTS, { recursive: true });
const browser = await chromium.launch({ args: ["--no-sandbox", "--disable-gpu"] });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const results = [];
async function step(name, fn) {
  try { await fn(); results.push(`OK   ${name}`); }
  catch (e) { results.push(`FAIL ${name}: ${e.message.split("\n")[0]}`); await page.screenshot({ path: `${SHOTS}fail-public-${name.replace(/\W+/g, "_")}.png`, fullPage: true }); }
}
const text = (t) => page.getByText(t, { exact: false }).first().waitFor({ timeout: 15000 });

await step("home con secciones y CTA", async () => {
  await page.goto(BASE);
  await text("Transformamos tu PyME");
  for (const t of ["Que resuelve el ERP", "Como funciona la demo", "NEXATEC Cloud", "Preguntas frecuentes"]) await text(t);
  const wa = await page.locator('a[href^="https://wa.me/"]').first().getAttribute("href");
  if (!wa.includes("595981813971")) throw new Error(`WhatsApp inesperado: ${wa}`);
  if (await page.locator('a[href="/admin/clientes"]').count()) throw new Error("menu publico expone el Control Center");
  await page.screenshot({ path: `${SHOTS}20-home.png`, fullPage: true });
});

await step("aviso de cookies se cierra y no vuelve", async () => {
  await page.getByRole("button", { name: "Entendido" }).click();
  await page.reload();
  await page.waitForTimeout(1500);
  if (await page.getByRole("button", { name: "Entendido" }).count()) throw new Error("el aviso volvio a aparecer");
});

await step("pedido de demo exige privacidad y se envia", async () => {
  await page.goto(`${BASE}/demo`);
  await text("Pedir demo");
  await page.getByPlaceholder("Nombre y apellido").fill("Prueba Automatizada");
  await page.getByPlaceholder("Email").fill(EMAIL);
  await page.getByPlaceholder("Empresa (opcional)").fill("Empresa E2E");
  await page.waitForTimeout(4000);
  await page.getByRole("button", { name: "Pedir mi demo" }).click();
  await text("aceptar la politica de privacidad");
  await page.locator('input[type="checkbox"]').check();
  await page.getByRole("button", { name: "Pedir mi demo" }).click();
  await text("Recibimos tu pedido");
  await page.screenshot({ path: `${SHOTS}21-demo-ok.png`, fullPage: true });
});

await step("paginas legales", async () => {
  for (const [p, t] of [["/privacidad", "Tus derechos"], ["/terminos", "Demo gratuita"], ["/cookies", "nexatec_session"]]) {
    await page.goto(`${BASE}${p}`);
    await text(t);
    await text("Borrador sujeto a revision legal");
  }
});

await step("movil sin desborde", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const p of ["/", "/demo"]) {
    await page.goto(`${BASE}${p}`);
    await page.waitForTimeout(1000);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (overflow > 2) throw new Error(`${p} desborda ${overflow}px`);
  }
  await page.goto(BASE);
  await page.screenshot({ path: `${SHOTS}22-home-movil.png`, fullPage: true });
});

await browser.close();
console.log(results.join("\n"));
console.log(errors.length ? `ERRORES JS:\n${errors.join("\n")}` : "sin errores de JavaScript");
