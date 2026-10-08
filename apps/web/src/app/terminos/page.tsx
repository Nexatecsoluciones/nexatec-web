import type { Metadata } from "next";
import { LegalPage } from "@/components/legal";

export const metadata: Metadata = { title: "Terminos y condiciones" };

export default function TerminosPage() {
  return (
    <LegalPage title="Terminos y condiciones">
      <h2>El servicio</h2>
      <p>NEXATEC ofrece sistemas de gestion (ERP, CRM, Business Intelligence y automatizacion) en modalidad de servicio en la nube, instalacion local o proyecto a medida, segun lo acordado con cada cliente.</p>
      <h2>Demo gratuita</h2>
      <ul>
        <li>Dura 14 dias (NEXATEC puede extenderla). Al vencer, el acceso se bloquea.</li>
        <li>Usa una empresa y datos <strong>ficticios</strong>. Los comprobantes que genera son de simulacion y <strong>no tienen validez tributaria</strong>.</li>
        <li>No se debe cargar en la demo informacion real sensible. Los datos de la demo no se trasladan como documentos reales a un sistema de produccion.</li>
      </ul>
      <h2>Comprobantes y obligaciones tributarias</h2>
      <p>Mientras la integracion con la facturacion electronica de la DNIT (SIFEN) no este habilitada, los comprobantes del sistema son documentos internos de gestion y no reemplazan la factura legal. El cliente es responsable de cumplir sus obligaciones tributarias; los informes contables son de gestion y deben ser revisados por su contador.</p>
      <h2>Contratacion y pagos</h2>
      <p>La contratacion se acuerda directamente con NEXATEC (por ejemplo por WhatsApp o email). Los precios publicados son de referencia; la propuesta final se confirma por escrito.</p>
      <h2>Datos del cliente</h2>
      <p>El cliente es responsable de los datos que carga y de quienes tienen acceso a su sistema. Puede pedir una copia exportable de sus datos y, al terminar el servicio, su eliminacion, segun la politica de privacidad.</p>
      <h2>Uso aceptable</h2>
      <p>No esta permitido intentar acceder a datos de otras empresas, vulnerar la seguridad del servicio ni usarlo para actividades ilicitas.</p>
      <h2>Disponibilidad</h2>
      <p>Trabajamos para que el servicio este disponible y respaldado diariamente, pero puede haber interrupciones por mantenimiento o causas ajenas.</p>
      <h2>Propiedad intelectual</h2>
      <p>El software, el diseno y las marcas de NEXATEC son de su titularidad. Los datos cargados por el cliente son del cliente.</p>
    </LegalPage>
  );
}
