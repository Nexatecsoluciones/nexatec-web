// E2E de la Etapa 2 (venta rapida, caja, etiquetas, adjuntos) en navegador real.
// Uso: node pos.e2e.mjs <credenciales.json>   (scripts/e2e_tenant.py create; trae session_cookie)
// E2E_BASE_URL (por defecto http://127.0.0.1:4312, entorno de prueba aislado).
import { chromium } from "playwright";
import fs from "node:fs";

const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const BASE = process.env.E2E_BASE_URL ?? "http://127.0.0.1:4312";
const SHOTS = process.env.E2E_SHOTS ?? new URL("./shots/", import.meta.url).pathname;
fs.mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch({ args: ["--no-sandbox", "--disable-gpu",
  ...(process.env.E2E_HOST_RULES ? [`--host-resolver-rules=${process.env.E2E_HOST_RULES}`] : [])] });
const context = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await context.addCookies([{ name: cfg.session_cookie.name, value: cfg.session_cookie.value, domain: new URL(BASE).hostname,
  path: "/", httpOnly: true, secure: BASE.startsWith("https"), sameSite: "Lax" }]);
const page = await context.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("dialog", (d) => d.accept("Prueba E2E"));

const results = [];
async function step(name, fn) {
  const t0 = Date.now();
  try {
    await fn();
    results.push(`OK   ${name} (${((Date.now() - t0) / 1000).toFixed(1)} s)`);
  } catch (e) {
    results.push(`FAIL ${name}: ${e.message.split("\n")[0]}`);
    await page.screenshot({ path: `${SHOTS}fail-pos-${name.replace(/\W+/g, "_")}.png`, fullPage: true });
  }
}
const erp = `${BASE}/erp/${cfg.access_id}`;
const text = (t, timeout = 15000) => (t instanceof RegExp ? page.getByText(t) : page.getByText(t, { exact: false })).first().waitFor({ timeout });
const search = () => page.getByLabel("Buscar producto o escanear codigo");

await step("abrir caja desde Vender", async () => {
  await page.goto(`${erp}/vender`);
  await text("Abrir caja");
  await page.locator("select").first().selectOption({ label: "CAJA-01 - Caja principal (demo)" });
  await page.getByLabel("Fondo inicial en efectivo (Gs.)").fill("100000");
  await page.getByRole("button", { name: "Abrir caja" }).click();
  await text("Turno CJ-");
});

await step("escanear codigo interno, cantidad con * y buscar por nombre", async () => {
  await search().fill("NX000001");
  await search().press("Enter");
  await text("Agua mineral 500 ml");
  await search().fill("2*BEB-GASEOSA-2L");
  await search().press("Enter");
  await text("Gaseosa cola 2 L");
  await search().fill("arroz");
  await page.getByRole("button", { name: /Arroz tipo 1/ }).click();
  await page.waitForFunction(() => document.body.innerText.includes("44.000"));
  await page.screenshot({ path: `${SHOTS}20-vender-carrito.png`, fullPage: true });
});

let saleTicket = null;
await step("cobrar en efectivo con vuelto (F2) e imprimir ticket", async () => {
  await page.getByLabel("Efectivo").fill("50000");
  await page.waitForFunction(() => document.body.innerText.includes("6.000"));
  await page.keyboard.press("F2");
  await text("Venta registrada");
  await text("Vuelto");
  await page.screenshot({ path: `${SHOTS}21-vender-ok.png`, fullPage: true });
  const [ticket] = await Promise.all([context.waitForEvent("page"), page.getByRole("link", { name: "Imprimir ticket" }).click()]);
  await ticket.getByText("SIN VALIDEZ TRIBUTARIA").first().waitFor({ timeout: 15000 });
  await ticket.getByText("Vuelto").first().waitFor();
  await ticket.screenshot({ path: `${SHOTS}22-ticket.png`, fullPage: true });
  saleTicket = ticket.url();
  await ticket.close();
});

await step("venta a credito a cliente identificado", async () => {
  await page.getByRole("button", { name: "Nueva venta" }).click();
  await search().fill("BEB-JUGO-1L");
  await search().press("Enter");
  await text("Jugo de naranja 1 L");
  await page.getByRole("button", { name: "Credito", exact: true }).click();
  await page.getByPlaceholder(/Cliente: Consumidor final/).fill("Comercial");
  await page.getByRole("button", { name: /Comercial Ficticia Uno/ }).click();
  await page.getByRole("button", { name: /Registrar venta a credito/ }).click();
  await text("Venta registrada");
});

await step("caja: gasto y cierre con conteo de billetes sin diferencia", async () => {
  await page.goto(`${erp}/caja`);
  await text("Debe haber en caja");
  const gasto = page.locator("form").filter({ has: page.getByRole("button", { name: "Registrar" }) });
  await gasto.locator("select").selectOption("EXPENSE");
  await gasto.getByLabel("Monto (Gs.)").fill("5000");
  await gasto.getByLabel("Detalle").fill("Compra de bolsas");
  await gasto.getByRole("button", { name: "Registrar" }).click();
  await text("Movimiento registrado");
  // Esperado: 100.000 + 44.000 - 5.000 = 139.000
  for (const [d, q] of [["100000", "1"], ["20000", "1"], ["10000", "1"], ["5000", "1"], ["2000", "2"]]) {
    await page.getByLabel(`Cantidad de ${d}`, { exact: true }).fill(q);
  }
  await page.waitForFunction(() => /Diferencia\s*\n?\s*Gs\. 0/.test(document.body.innerText) || document.body.innerText.includes("Gs. 0"));
  await page.screenshot({ path: `${SHOTS}23-caja-cierre.png`, fullPage: true });
  await page.getByRole("button", { name: "Cerrar turno" }).click();
  await text("Turno cerrado");
  await text("Arqueo del turno");
});

await step("tablero del dia (supervisor)", async () => {
  await page.getByRole("button", { name: "Tablero del dia" }).click();
  await text("Ticket promedio");
  await text("Productos mas vendidos");
  await page.screenshot({ path: `${SHOTS}24-caja-tablero.png`, fullPage: true });
});

await step("etiquetas con codigo de barras", async () => {
  await page.goto(`${erp}/productos`);
  await text("Agua mineral 500 ml");
  await page.getByLabel("Elegir Agua mineral 500 ml para etiquetas").check();
  await page.getByLabel("Elegir Gaseosa cola 2 L para etiquetas").check();
  const [lab] = await Promise.all([context.waitForEvent("page"), page.getByRole("link", { name: /Imprimir etiquetas \(2\)/ }).click()]);
  await lab.getByText("2 etiqueta(s)").waitFor({ timeout: 15000 });
  const bars = await lab.locator("svg[aria-label^='Codigo de barras']").count();
  if (bars !== 2) throw new Error(`se esperaban 2 codigos de barras, hay ${bars}`);
  await lab.screenshot({ path: `${SHOTS}25-etiquetas.png`, fullPage: true });
  await lab.close();
});

await step("adjuntar PDF a factura de proveedor", async () => {
  await page.goto(`${erp}/pagos`);
  await text("Facturas de proveedor pendientes");
  await page.getByRole("button", { name: "Adjuntos" }).first().click();
  await text("Adjuntos de la factura");
  await page.locator("input[type=file][capture]").setInputFiles({ name: "factura-proveedor.pdf", mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n") });
  await text("Archivo adjuntado.");
  await text("factura-proveedor.pdf");
  await page.screenshot({ path: `${SHOTS}26-adjuntos.png`, fullPage: true });
});

await step("movil (390 px): Vender sin desborde", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${erp}/caja`);
  await page.goto(`${erp}/vender`);
  await text(/Abrir caja|Escanear codigo/);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  await page.screenshot({ path: `${SHOTS}27-vender-movil.png`, fullPage: true });
  if (overflow > 2) throw new Error(`desborda ${overflow}px`);
});

await browser.close();
console.log(results.join("\n"));
console.log(saleTicket ? "ticket: ok" : "ticket: -");
console.log(errors.length ? `ERRORES DE CONSOLA:\n${[...new Set(errors)].join("\n")}` : "sin errores de consola");
