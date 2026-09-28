const WHATSAPP_PHONE = "595981813971";

function openWhatsApp(message) {
  const encodedMessage = encodeURIComponent(message);
  const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);

  const url = isMobile
    ? `https://wa.me/${WHATSAPP_PHONE}?text=${encodedMessage}`
    : `https://web.whatsapp.com/send?phone=${WHATSAPP_PHONE}&text=${encodedMessage}`;

  window.open(url, "_blank", "noopener,noreferrer");
}
