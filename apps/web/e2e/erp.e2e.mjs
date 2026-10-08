// E2E de la interfaz del ERP contra staging real (Chromium headless). Ver docs/E2E.md.
// Uso: node erp.e2e.mjs <credenciales.json>   (generado por apps/api/scripts/e2e_tenant.py)
import { chromium } from "playwright";
import fs from "node:fs";

const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const BASE = process.env.E2E_BASE_URL ?? "https://nexatecpy.com";
const SHOTS = process.env.E2E_SHOTS ?? new URL("./shots/", import.meta.url).pathname;
fs.mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch({
  // E2E_HOST_RULES="MAP nexatecpy.com 104.21.50.187": util si el resolver local tiene cacheado un NXDOMAIN.
  args: ["--no-sandbox", "--disable-gpu", ...(process.env.E2E_HOST_RULES ? [`--host-resolver-rules=${process.env.E2E_HOST_RULES}`] : [])],
});
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
page.on("dialog", (d) => d.accept("Prueba E2E automatizada"));

const results = [];
async function step(name, fn) {
  try {
    await fn();
    results.push(`OK   ${name}`);
  } catch (e) {
    results.push(`FAIL ${name}: ${e.message.split("\n")[0]}`);
    await page.screenshot({ path: `${SHOTS}fail-${name.replace(/\W+/g, "_")}.png`, fullPage: true });
  }
}
const erp = `${BASE}/erp/${cfg.access_id}`;
const expectText = async (text, timeout = 15000) => page.getByText(text, { exact: false }).first().waitFor({ timeout });

await step("login", async () => {
  if (cfg.session_cookie) {
    // Produccion con Turnstile real: el navegador automatizado no puede pasar
    // el desafio. La sesion la emite scripts/e2e_tenant.py en el servidor.
    const host = new URL(BASE).hostname;
    await page.context().addCookies([{ name: cfg.session_cookie.name, value: cfg.session_cookie.value, domain: host,
      path: "/", httpOnly: true, secure: BASE.startsWith("https"), sameSite: "Lax" }]);
    await page.goto(`${BASE}/portal`);
    await page.waitForURL(/\/portal/, { timeout: 20000 });
    return;
  }
  await page.goto(`${BASE}/login`);
  await page.locator('input[type="email"]').fill(cfg.email);
  await page.locator('input[type="password"]').fill(cfg.password);
  // Turnstile de prueba: esperar a que el widget entregue el token.
  await page.waitForTimeout(4000);
  await page.getByRole("button", { name: /ingresar|entrar|iniciar/i }).click();
  await page.waitForURL(/\/portal/, { timeout: 20000 });
});

await step("portal abre la demo", async () => {
  await expectText("NEXATEC ERP (prueba E2E)");
  await page.getByRole("button", { name: /probar demo/i }).click();
  await page.waitForURL(/\/erp\//, { timeout: 15000 });
  await expectText("DEMO");
  await expectText("SIMULACIÓN");
  await page.screenshot({ path: `${SHOTS}01-tablero.png`, fullPage: true });
});

await step("tablero con KPIs", async () => {
  await expectText("Ventas (IVA incl.)");
  await expectText("Margen bruto");
  const txt = await page.locator("main").innerText();
  if (!/Gs\.\s?[1-9]/.test(txt)) throw new Error("no hay montos en el tablero");
});

for (const [path, marker] of [["productos", "Agua mineral"], ["terceros", "Comercial Ficticia Uno"], ["inventario", "Kardex"],
  ["cobranzas", "SIN VALIDEZ TRIBUTARIA"], ["compras", "OC-"], ["pagos", "PP-000001"], ["contabilidad", "Balance de comprobacion"]]) {
  await step(`pantalla ${path}`, async () => {
    await page.goto(`${erp}/${path}`);
    await expectText(marker);
    await page.screenshot({ path: `${SHOTS}02-${path}.png`, fullPage: true });
  });
}

let orderNumber = "";
await step("venta completa por la interfaz", async () => {
  await page.goto(`${erp}/ventas`);
  await expectText("Nuevo pedido");
  const form = page.locator("form").first();
  const selects = form.locator("select");
  await selects.nth(0).selectOption({ label: "Comercial Ficticia Uno S.A." });
  await selects.nth(1).selectOption({ index: 1 });
  await selects.nth(2).selectOption("CASH");
  await selects.nth(3).selectOption({ index: 1 }); // primer producto
  await form.locator('input[placeholder="Cantidad"]').first().fill("2");
  await page.getByRole("button", { name: "Crear pedido" }).click();
  await expectText("Pedido creado en borrador");
  const title = await page.getByText(/Pedido OV-\d+/).first().innerText();
  orderNumber = title.replace("Pedido ", "");
  await page.getByRole("button", { name: "Confirmar" }).click();
  await expectText("stock reservado");
  await page.getByRole("button", { name: "Entregar" }).click();
  await expectText("Pedido entregado");
  await page.getByRole("button", { name: "Facturar" }).click();
  await expectText("Factura interna emitida");
  await page.screenshot({ path: `${SHOTS}03-venta.png`, fullPage: true });
});

await step("cobro de la factura nueva", async () => {
  await page.goto(`${erp}/cobranzas`);
  await expectText("Registrar cobro");
  const form = page.locator("form").first();
  await form.locator("select").nth(0).selectOption({ label: "Comercial Ficticia Uno S.A." });
  await page.waitForTimeout(1500);
  const opts = await form.locator("select").nth(1).locator("option").allInnerTexts();
  const idx = opts.findIndex((o) => o.startsWith("FI-"));
  if (idx < 0) throw new Error("no aparece la factura para aplicar");
  await form.locator("select").nth(1).selectOption({ index: idx });
  await page.getByRole("button", { name: "Registrar" }).first().click();
  await expectText("Cobro registrado y aplicado");
});

await step("nota de credito por la interfaz", async () => {
  await page.goto(`${erp}/cobranzas`);
  await page.getByText("Solo con saldo pendiente").waitFor({ timeout: 15000 });
  await page.locator('input[type="checkbox"]').first().uncheck();
  await page.waitForTimeout(1500);
  await page.getByRole("button", { name: /^FI-/ }).first().click();
  await expectText("Emitir nota de credito");
  const cnForm = page.locator("form").filter({ has: page.getByRole("button", { name: "Emitir nota de credito" }) });
  await cnForm.locator('input[type="number"]:not([disabled])').first().fill("1");
  await cnForm.getByPlaceholder("Motivo").fill("Devolucion E2E");
  await page.getByRole("button", { name: "Emitir nota de credito" }).click();
  await expectText("Nota de credito emitida");
  await page.screenshot({ path: `${SHOTS}06-nota-credito.png`, fullPage: true });
});

await step("vista de impresion con marca de agua y PDF", async () => {
  const [tab] = await Promise.all([page.context().waitForEvent("page"), page.getByText("Imprimir / PDF").first().click()]);
  await tab.getByText("DOCUMENTO DE SIMULACIÓN — SIN VALIDEZ TRIBUTARIA").first().waitFor({ timeout: 15000 });
  await tab.getByText("FICTICIO").first().waitFor({ timeout: 5000 });
  await tab.screenshot({ path: `${SHOTS}07-factura-impresion.png`, fullPage: true });
  const pdf = await tab.pdf({ format: "A4", printBackground: true });
  if (pdf.length < 10000) throw new Error(`PDF demasiado chico (${pdf.length} bytes)`);
  fs.writeFileSync(`${SHOTS}factura.pdf`, pdf);
  await tab.close();
});

await step("contabilidad cuadra", async () => {
  await page.goto(`${erp}/contabilidad`);
  await page.getByRole("button", { name: "Balance general" }).click();
  await expectText("Activo = Pasivo + Patrimonio");
  await page.screenshot({ path: `${SHOTS}04-balance.png`, fullPage: true });
});

await step("CRM: embudo, ganar oportunidad, prospecto a cliente", async () => {
  await page.goto(`${erp}/crm`);
  await expectText("Pronostico");
  await page.getByRole("button", { name: /Provision mensual de bebidas/ }).click();
  await expectText("Confirmar volumen mensual");
  await page.getByRole("button", { name: "Ganada", exact: true }).click();
  await expectText("Oportunidad ganada.");
  const leadForm = page.locator("form").filter({ has: page.getByRole("button", { name: "Cargar prospecto" }) });
  await leadForm.getByLabel("Contacto").fill("Prospecto E2E");
  await leadForm.getByLabel("Empresa").fill("Empresa E2E Ficticia");
  await leadForm.getByLabel("Telefono").fill("(000) 000-000");
  await page.getByRole("button", { name: "Cargar prospecto" }).click();
  await expectText("Prospecto cargado.");
  await page.getByRole("row", { name: /Empresa E2E Ficticia/ }).getByRole("button", { name: "A cliente" }).click();
  await expectText("Prospecto convertido en cliente");
  await page.screenshot({ path: `${SHOTS}04-crm.png`, fullPage: true });
  await page.goto(`${erp}/terceros`);
  await expectText("Empresa E2E Ficticia");
});

await step("RR.HH.: personal, asistencia y planilla cerrada con recibos", async () => {
  await page.goto(`${erp}/rrhh`);
  await expectText("Gomez Ejemplo, Juan Carlos");
  await page.screenshot({ path: `${SHOTS}06-rrhh-personal.png`, fullPage: true });
  await page.goto(`${erp}/rrhh/asistencia`);
  await page.locator("select").first().selectOption({ label: "OB-01 - Edificio Ejemplo Centro (demo)" });
  await expectText("Horas pagas del periodo");
  await expectText("Benitez Ficticio, Pedro");
  await page.screenshot({ path: `${SHOTS}07-rrhh-asistencia.png`, fullPage: true });
  await page.goto(`${erp}/rrhh/planillas`);
  await page.getByRole("button", { name: "PL-000001" }).click();
  await expectText("Total a pagar");
  await expectText("Cerrada");
  const [recibos] = await Promise.all([
    page.context().waitForEvent("page"),
    page.getByRole("link", { name: "Recibos para imprimir" }).click(),
  ]);
  await recibos.getByText("RECIBO DE PAGO").first().waitFor({ timeout: 15000 });
  await recibos.getByText("DEMO · DATOS FICTICIOS").first().waitFor({ timeout: 5000 });
  const count = await recibos.getByText("RECIBO DE PAGO").count();
  if (count !== 8) throw new Error(`se esperaban 8 recibos, hay ${count}`);
  await recibos.screenshot({ path: `${SHOTS}08-rrhh-recibos.png` });
  await recibos.close();
});

await step("RR.HH.: habilitar celular de obra y marcar con C.I.", async () => {
  await page.goto(`${erp}/rrhh/obras`);
  await expectText("Celulares para marcar asistencia");
  const devForm = page.locator("form").filter({ has: page.getByRole("button", { name: "Habilitar" }) });
  await devForm.locator("select").selectOption({ label: "OB-01 - Edificio Ejemplo Centro (demo)" });
  await devForm.getByLabel("Nombre del aparato").fill("Tablet E2E");
  await page.getByRole("button", { name: "Habilitar" }).click();
  await expectText("se muestra una sola vez");
  const link = (await page.locator("p.font-mono").innerText()).trim();
  // Contexto aparte, sin la sesion del admin: como el celular real de la obra.
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  await phone.goto(link);
  await phone.getByText("Edificio Ejemplo Centro (demo)").first().waitFor({ timeout: 15000 });
  for (const d of "9991001") await phone.getByRole("button", { name: d, exact: true }).click();
  await phone.getByRole("button", { name: "Marcar entrada / salida" }).click();
  await phone.getByText(/(Entrada|Salida) registrada/).waitFor({ timeout: 15000 });
  await phone.getByText("Gomez Ejemplo, Juan Carlos").waitFor({ timeout: 5000 });
  const overflow = await phone.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  await phone.screenshot({ path: `${SHOTS}09-marcacion-celular.png`, fullPage: true });
  if (overflow > 2) throw new Error(`la marcacion desborda ${overflow}px`);
  await phone.close();
});

await step("movil (390px) sin scroll horizontal de pagina", async () => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${erp}`);
  await expectText("Ventas (IVA incl.)");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  await page.screenshot({ path: `${SHOTS}05-movil.png`, fullPage: true });
  if (overflow > 2) throw new Error(`la pagina desborda ${overflow}px`);
  await page.goto(`${erp}/crm`);
  await expectText("Pronostico");
  const crmOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  await page.screenshot({ path: `${SHOTS}05-movil-crm.png`, fullPage: true });
  if (crmOverflow > 2) throw new Error(`el CRM desborda ${crmOverflow}px`);
});

await browser.close();
console.log(results.join("\n"));
console.log(`orden: ${orderNumber}`);
console.log(errors.length ? `ERRORES DE CONSOLA:\n${[...new Set(errors)].join("\n")}` : "sin errores de consola");
