import type { Metadata } from "next";
import { LegalPage } from "@/components/legal";

export const metadata: Metadata = { title: "Cookies" };

export default function CookiesPage() {
  return (
    <LegalPage title="Politica de cookies">
      <p>Este sitio usa solo lo estrictamente necesario para funcionar. No usamos cookies de publicidad, de seguimiento ni de estadisticas de terceros.</p>
      <h2>Que se guarda en tu navegador</h2>
      <ul>
        <li><strong>nexatec_session</strong> (cookie, HttpOnly): mantiene tu sesion iniciada. Dura hasta 8 horas o hasta que salis.</li>
        <li><strong>nx-cookie-notice-v1</strong> (almacenamiento local): recuerda que ya viste este aviso.</li>
        <li>Cloudflare puede usar cookies tecnicas de seguridad y anti-bots (por ejemplo al verificar que no sos un robot).</li>
      </ul>
      <p>Si en el futuro agregamos herramientas de estadisticas o marketing, no se van a activar sin tu consentimiento previo.</p>
    </LegalPage>
  );
}
