// E2E del primer ingreso de un SUPER_ADMIN (ver docs/E2E.md):
// contrasena -> cambio obligatorio -> el Control Center exige MFA -> alta del
// MFA leyendo la clave de la pantalla -> salir -> entrar con codigo TOTP.
// Uso: node admin-first-login.e2e.mjs <credenciales.json>  (scripts/e2e_tenant.py create-admin)
import { chromium } from "playwright";
import crypto from "node:crypto";
import fs from "node:fs";

const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const BASE = "https://staging.nexatecpy.com";
const SHOTS = process.env.E2E_SHOTS ?? new URL("./shots/", import.meta.url).pathname;
fs.mkdirSync(SHOTS, { recursive: true });
const NEW_PASSWORD = `${cfg.password}Z9x`;

// TOTP RFC 6238 (mismo algoritmo que app/security/totp.py).
function b32(s) {
  const a = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of s.replace(/\s|=/g, "").toUpperCase()) bits += a.indexOf(ch).toString(2).padStart(5, "0");
  return Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
}
function totp(secret, offset = 0) {
  const step = Math.floor(Date.now() / 30000) + offset;
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(step));
  const h = crypto.createHmac("sha1", b32(secret)).update(msg).digest();
  const o = h[h.length - 1] & 0xf;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

const browser = await chromium.launch({ args: ["--no-sandbox", "--disable-gpu"] });
const page = await browser.newPage({ viewport: { width: 1300, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
const results = [];
async function step(name, fn) {
  try { await fn(); results.push(`OK   ${name}`); }
  catch (e) { results.push(`FAIL ${name}: ${e.message.split("\n")[0]}`); await page.screenshot({ path: `${SHOTS}fail-admin-${name.replace(/\W+/g, "_")}.png`, fullPage: true }); }
}
const text = (t) => page.getByText(t, { exact: false }).first().waitFor({ timeout: 15000 });

async function login(password) {
  await page.goto(`${BASE}/login`);
  await page.locator('input[type="email"]').fill(cfg.email);
  await page.locator('input[type="password"]').fill(password);
  await page.waitForTimeout(4000);
  await page.getByRole("button", { name: /ingresar/i }).click();
}

let secret = "";
await step("primer ingreso pide cambio de contrasena", async () => {
  await login(cfg.password);
  await text("Elegi una contrasena nueva");
  const inputs = page.locator('input[type="password"]');
  await inputs.nth(0).fill(cfg.password);
  await inputs.nth(1).fill(NEW_PASSWORD);
  await inputs.nth(2).fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Guardar" }).click();
});

await step("Control Center lleva a configurar MFA", async () => {
  await page.waitForURL(/\/admin\/seguridad/, { timeout: 20000 });
  await text("tenes que activar la verificacion en dos pasos");
  await page.screenshot({ path: `${SHOTS}10-admin-mfa-obligatorio.png`, fullPage: true });
});

await step("alta de MFA con la clave mostrada", async () => {
  await page.getByRole("button", { name: "Configurar" }).click();
  const shown = await page.locator("p.font-mono").innerText();
  secret = shown.replace(/\s/g, "");
  await page.locator('input[placeholder="Codigo de 6 digitos"]').fill(totp(secret));
  await page.getByRole("button", { name: "Activar" }).click();
  await text("MFA activado");
});

await step("con MFA el Control Center abre", async () => {
  await page.goto(`${BASE}/admin/clientes`);
  await page.waitForTimeout(3000);
  if (page.url().includes("/admin/seguridad")) throw new Error("sigue redirigiendo a seguridad");
});

await step("salir y volver a entrar pide codigo", async () => {
  await page.context().clearCookies();
  await login(NEW_PASSWORD);
  await text("Verificacion en dos pasos");
  await page.locator('input[autocomplete="one-time-code"]').fill("000000");
  await page.getByRole("button", { name: "Verificar" }).click();
  await text("Codigo incorrecto");
  await page.locator('input[autocomplete="one-time-code"]').fill(totp(secret, 1));
  await page.getByRole("button", { name: "Verificar" }).click();
  await page.waitForURL(/\/admin/, { timeout: 15000 });
  await page.screenshot({ path: `${SHOTS}11-admin-dentro.png`, fullPage: true });
});

await browser.close();
console.log(results.join("\n"));
console.log(errors.length ? `ERRORES JS:\n${errors.join("\n")}` : "sin errores de JavaScript");
