import type { Metadata } from "next";
import { LegalPage } from "@/components/legal";

export const metadata: Metadata = { title: "Politica de privacidad" };

export default function PrivacidadPage() {
  return (
    <LegalPage title="Politica de privacidad">
      <h2>Que datos tratamos</h2>
      <ul>
        <li><strong>Pedido de demo o contacto:</strong> nombre, email, telefono, empresa y mensaje, la version de esta politica que aceptaste y la direccion IP desde la que se envio (para evitar abusos).</li>
        <li><strong>Cuenta de usuario:</strong> email, nombre, contrasena (guardada solo como hash, nunca en claro) y, si la activas, la clave de verificacion en dos pasos (cifrada).</li>
        <li><strong>Seguridad y auditoria:</strong> inicios de sesion, acciones realizadas, IP y navegador, para proteger las cuentas y poder investigar incidentes.</li>
        <li><strong>Datos de negocio de cada empresa cliente</strong> (productos, clientes, ventas, etc.): los carga la empresa en su propio sistema. Respecto de esos datos, NEXATEC actua por cuenta de la empresa cliente, que es responsable de ellos.</li>
      </ul>
      <h2>Para que los usamos</h2>
      <ul>
        <li>Responder tu pedido, habilitar la demo y prestar el servicio contratado.</li>
        <li>Enviarte emails necesarios del servicio: invitaciones, recuperacion de contrasena y avisos de vencimiento de la demo.</li>
        <li>Seguridad, prevencion de fraude y cumplimiento de obligaciones legales.</li>
      </ul>
      <p>No vendemos datos personales ni los usamos para publicidad de terceros.</p>
      <h2>Donde se guardan y con quien se comparten</h2>
      <ul>
        <li>Servidores en Oracle Cloud Infrastructure. Cada empresa cliente tiene su propia base de datos, separada de las demas.</li>
        <li>Cloudflare (red, proteccion anti-bots y acceso al sitio).</li>
        <li>Brevo (envio de emails transaccionales), cuando este habilitado.</li>
      </ul>
      <h2>Cuanto tiempo los conservamos</h2>
      <ul>
        <li>Backups diarios cifrados, con 14 dias de retencion.</li>
        <li>Las demos vencidas no se borran en el acto: se conservan un tiempo para poder extenderlas o pasarlas a un sistema real, y despues se eliminan.</li>
        <li>Los registros de auditoria se conservan el tiempo necesario por seguridad y obligaciones legales.</li>
      </ul>
      <h2>Tus derechos</h2>
      <p>Podes pedir acceso, rectificacion, actualizacion, eliminacion o una copia exportable de tus datos personales, y oponerte a su tratamiento cuando corresponda, escribiendo al email de contacto. Respondemos conforme a la normativa paraguaya de proteccion de datos personales aplicable.</p>
      <h2>Seguridad</h2>
      <p>Conexiones cifradas, contrasenas con hash, verificacion en dos pasos obligatoria para el personal de NEXATEC, bases separadas por empresa, permisos por rol y backups cifrados. Ningun sistema es invulnerable; si detectamos un incidente que afecte tus datos, te avisamos.</p>
    </LegalPage>
  );
}
