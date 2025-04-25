import streamlit as st
import requests
import os
import zipfile
import io
from urllib.parse import urljoin, urlparse, urlencode
import re
from xhtml2pdf import pisa
from bs4 import BeautifulSoup

# --- Configuración API ---
API_BASE_URL = "http://crm.sugos.com.ve/"
LOGIN_ENDPOINT = "custom/apps/api.php?login"
CONSULTA_ENDPOINT_URL = urljoin(API_BASE_URL, "custom/apps/api.php")
DOWNLOAD_BASE_URL = "http://crm.sugos.com.ve/"

# --- Funciones de Ayuda ---

def get_api_token(username, password):
    """Autentica contra la API (POST) y devuelve el token."""
    login_url = urljoin(API_BASE_URL, LOGIN_ENDPOINT)
    payload = {"username": username, "password": password}
    headers = {'Content-Type': 'application/json'}
    try:
        response = requests.post(login_url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        token = data.get("token") or data.get("access_token") or data.get("data", {}).get("token")
        if not token:
            st.error(f"No se pudo encontrar el token en la respuesta de login: {data}")
            return None
        st.success("Autenticación exitosa.")
        return token
    except requests.exceptions.RequestException as e:
        st.error(f"Error HTTP durante la autenticación: {e}")
        # Intentar mostrar respuesta si está definida
        try:
            if 'response' in locals():
                st.error(f"Respuesta del servidor: {response.text}")
        except Exception as resp_e:
            st.warning(f"No se pudo obtener texto de respuesta: {resp_e}")
        return None
    except Exception as e:
        st.error(f"Error inesperado al procesar login: {e}")
        return None

def get_orders_for_cedula(token, cedula):
    """Obtiene la LISTA de órdenes de servicio para una cédula (GET con BODY)."""
    action_params = {"afn": "ordermanager", "cfn": "aps-consulta"}
    target_url = f"{CONSULTA_ENDPOINT_URL}?{urlencode(action_params)}"
    payload = {
        "page-id": "existing-orders-page",
        "section-id": "existing-orders",
        "order-keyword": str(cedula).strip()
    }
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    orders = []
    try:
        response = requests.get(target_url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "OK" and "data" in data and "existing-orders" in data["data"]:
            records = data["data"]["existing-orders"].get("Records", [])
            if not records:
                return []
            orders = [{"id": rec.get("ID"), "tipo_servicio": rec.get("Carrier")}
                      for rec in records if isinstance(rec, dict) and rec.get("ID")]
            return orders
        else:
            st.warning(f"[get_orders] Respuesta no OK/formato inesperado para {cedula}: {data.get('message', 'Sin mensaje')}")
            return []
    except requests.exceptions.RequestException as e:
        st.error(f"[get_orders] Error HTTP GET para {cedula}: {e}")
        try:
            if 'response' in locals():
                st.error(f"Respuesta del servidor: {response.text}")
        except Exception as resp_e:
            st.warning(f"No se pudo obtener texto de respuesta: {resp_e}")
        return []
    except Exception as e:
        st.error(f"[get_orders] Error inesperado procesando {cedula}: {e}")
        # Intentar mostrar data si el error fue después de obtenerla
        try:
            if 'data' in locals():
                 st.write("Datos que causaron el error:", data)
        except Exception as data_e:
             st.warning(f"No se pudo mostrar 'data': {data_e}")
        return []

def get_order_details_and_attachments(token, order_id):
    """Obtiene DETALLES de UNA orden (GET con BODY) y extrae info de anexos y links."""
    action_params = {"afn": "ordermanager", "cfn": "aps-consulta"}
    target_url = f"{CONSULTA_ENDPOINT_URL}?{urlencode(action_params)}"
    payload = {"page-id": "existing-orders-page", "section-id": "existing-orders", "order-id": str(order_id)}
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    attachments_info = []
    links_info = []
    try:
        response = requests.get(target_url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "OK" and "data" in data:
            order_details = data.get("data", {}).get("existing-orders", {})
            if not order_details or not isinstance(order_details, dict):
                st.warning(f"[get_details] Estructura inesperada orden {order_id}.")
                return [], []
            # Anexos
            attachments_list = order_details.get("Attachments")
            if attachments_list and isinstance(attachments_list, list):
                for att in attachments_list:
                    if isinstance(att, dict):
                        f_id = att.get("ID")
                        f_name = att.get("FileName")
                        f_path = att.get("FolderPath")
                        if f_id and f_name and f_path:
                            f_url_name = f"{f_id}_{f_name}"
                            rel_path = f"{f_path.strip('/')}/{f_url_name}"
                            dl_url = urljoin(DOWNLOAD_BASE_URL, rel_path)
                            attachments_info.append({"type": "attachment", "id": f_id, "file_name": f_name, "download_url": dl_url})
                        else:
                            st.warning(f"[get_details] Anexo incompleto orden {order_id}: {att}")
                    else:
                        st.warning(f"[get_details] Elemento inesperado anexos orden {order_id}: {att}")
            # Links
            links_list = order_details.get("Links")
            if links_list and isinstance(links_list, list):
                for link in links_list:
                    if isinstance(link, dict):
                        l_name = link.get("Name")
                        rel_url = link.get("URL")
                        if l_name and rel_url:
                            abs_url = urljoin(API_BASE_URL, rel_url.lstrip('/'))
                            links_info.append({"type": "link", "name": l_name, "url": abs_url, "order_id": order_id})
                        else:
                            st.warning(f"[get_details] Link incompleto orden {order_id}: {link}")
        else:
            st.warning(f"[get_details] Respuesta GET no OK orden {order_id}: {data.get('message', 'Sin mensaje')}")
        return attachments_info, links_info
    except requests.exceptions.RequestException as e:
        st.error(f"[get_details] Error HTTP GET orden {order_id}: {e}")
        try:
            if 'response' in locals():
                st.error(f"Respuesta del servidor: {response.text}")
        except Exception as resp_e:
             st.warning(f"No se pudo obtener texto de respuesta: {resp_e}")
        return [], []
    except Exception as e:
        st.error(f"[get_details] Error inesperado procesando orden {order_id}: {e}")
        try:
            if 'data' in locals():
                 st.write("Datos que causaron el error:", data)
        except Exception as data_e:
             st.warning(f"No se pudo mostrar 'data': {data_e}")
        return [], []

def sanitize_filename(filename):
    """Elimina caracteres inválidos y limita longitud."""
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename)
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    return sanitized[:100]

def download_file_to_zip(token, download_url, zip_file_handle, zip_path):
    """Descarga un ANEXO (GET) y lo escribe en un ZipFile."""
    headers = {'Authorization': f'Bearer {token}'}
    try:
        response = requests.get(download_url, headers=headers, stream=True, timeout=180)
        response.raise_for_status()
        zip_file_handle.writestr(zip_path, response.content)
        return True
    except requests.exceptions.RequestException as e:
        st.error(f"Error descargando anexo {os.path.basename(zip_path)}: {e}")
        return False
    except Exception as e:
        st.error(f"Error añadiendo anexo {os.path.basename(zip_path)} al zip: {e}")
        return False

def process_link_item(token, link_info, zip_file_handle, base_zip_path):
    """
    Procesa un item de tipo 'link'. Descarga el contenido.
    Si es PDF, lo guarda directamente.
    Si es HTML, busca un iframe, descarga el contenido del iframe y lo convierte a PDF.
    Guarda el resultado usando el 'base_zip_path' proporcionado + extensión.
    """
    link_url = link_info["url"]
    link_name = link_info["name"] # Usado para mensajes
    zip_folder = link_info["cedula"] # Carpeta dentro del ZIP (ya incluida en base_zip_path)

    headers = {'Authorization': f'Bearer {token}'}

    try:
        # 1. Petición inicial al link
        response = requests.get(link_url, headers=headers, timeout=120)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').lower()

        # 2. Verificar si es PDF directamente
        if 'application/pdf' in content_type:
            st.write(f"  -> Link '{link_name}' es PDF directo.")
            pdf_zip_path = base_zip_path + ".pdf"
            zip_file_handle.writestr(pdf_zip_path, response.content)
            return True

        # 3. Si es HTML, procesar para encontrar iframe
        elif 'text/html' in content_type:
            st.write(f"  -> Link '{link_name}' es HTML. Buscando iframe...")
            html_content = response.content
            soup = BeautifulSoup(html_content, 'html.parser')
            iframe = soup.find('iframe')

            if iframe and iframe.get('src'):
                iframe_src = iframe['src']
                iframe_url = urljoin(link_url, iframe_src) # Usa la URL base del link original
                st.write(f"     -> Iframe encontrado. Descargando de: {iframe_url}")
                try:
                    # 4. Descargar contenido del iframe
                    iframe_response = requests.get(iframe_url, headers=headers, timeout=120)
                    iframe_response.raise_for_status()
                    iframe_content = iframe_response.content

                    # 5. Convertir contenido del iframe a PDF
                    st.write(f"     -> Convirtiendo contenido iframe a PDF...")
                    pdf_buffer = io.BytesIO()
                    iframe_content_bytes = iframe_content if isinstance(iframe_content, bytes) else iframe_content.encode('utf-8')
                    pisa_status = pisa.CreatePDF(io.BytesIO(iframe_content_bytes), dest=pdf_buffer, encoding='utf-8')

                    if pisa_status.err:
                        st.error(f"Error convirtiendo iframe de '{link_name}' a PDF: {pisa_status.err}")
                        fallback_zip_path = base_zip_path + "_iframe.html"
                        st.warning(f"Guardando contenido iframe como HTML: {fallback_zip_path}")
                        zip_file_handle.writestr(fallback_zip_path, iframe_content_bytes)
                        return False # Error

                    pdf_buffer.seek(0)
                    pdf_zip_path = base_zip_path + ".pdf" # Usar el nombre base secuencial
                    zip_file_handle.writestr(pdf_zip_path, pdf_buffer.read())
                    st.write(f"     -> PDF del iframe guardado.")
                    return True
                except requests.exceptions.RequestException as e_iframe:
                    st.error(f"Error descargando iframe ({iframe_url}) para '{link_name}': {e_iframe}")
                    return False
                except Exception as e_conv:
                    st.error(f"Error convirtiendo/guardando iframe de '{link_name}': {e_conv}")
                    return False
            else:
                # 6. Fallback: No se encontró iframe, convertir HTML principal
                st.warning(f"  -> No se encontró iframe en '{link_name}'. Convirtiendo HTML principal a PDF...")
                pdf_buffer = io.BytesIO()
                html_content_bytes = html_content if isinstance(html_content, bytes) else html_content.encode('utf-8')
                pisa_status = pisa.CreatePDF(io.BytesIO(html_content_bytes), dest=pdf_buffer, encoding='utf-8')

                if pisa_status.err:
                    st.error(f"Error convirtiendo HTML principal de '{link_name}' a PDF: {pisa_status.err}")
                    fallback_zip_path = base_zip_path + "_main.html"
                    st.warning(f"Guardando HTML principal: {fallback_zip_path}")
                    zip_file_handle.writestr(fallback_zip_path, html_content_bytes)
                    return False # Error

                pdf_buffer.seek(0)
                pdf_zip_path = base_zip_path + ".pdf" # Usar el nombre base secuencial
                zip_file_handle.writestr(pdf_zip_path, pdf_buffer.read())
                st.write(f"     -> PDF del HTML principal guardado (fallback).")
                return True
        else:
            # 7. Tipo de contenido desconocido
            st.warning(f"  -> Tipo desconocido ('{content_type}') para link '{link_name}'. Guardando binario.")
            fallback_zip_path = base_zip_path + ".bin" # Usar el nombre base secuencial
            zip_file_handle.writestr(fallback_zip_path, response.content)
            return True # Se guardó algo

    except requests.exceptions.RequestException as e:
        st.error(f"Error procesando link '{link_name}' ({link_url}): {e}")
        return False
    except Exception as e:
        st.error(f"Error inesperado procesando link '{link_name}' ({link_url}): {e}")
        return False


# --- Interfaz de Streamlit ---

st.set_page_config(page_title="Descarga Anexos y Links PDF SUGOS", layout="wide")
st.title("CRM SUGOS Downloader v0.0.1")
st.markdown("""
Ingrese cédulas. Se descargarán anexos y links asociados.
- **Anexos**: Archivos originales.
- **Links**:
    - Si la URL es **PDF**, se guarda directamente.
    - Si es **HTML**, se busca `<iframe>`, se descarga su contenido y se convierte a **PDF**.    
- **Nomenclatura**: Archivos se nombran `CEDULA/CEDULA-SECUENCIA.extension`.
- **Estructura**: ZIP con **carpetas por cédula**.
""")

try:
    USERNAME = st.secrets["api_credentials"]["username"]
    PASSWORD = st.secrets["api_credentials"]["password"]
except KeyError:
    st.error("Error: Credenciales API ('username', 'password') no configuradas en los secretos.")
    st.info("Configure los secretos en Streamlit Cloud o en .streamlit/secrets.toml localmente.")
    st.stop()
except Exception as e:
     st.error(f"Error al leer secretos: {e}")
     st.stop()

cedulas_input = st.text_area("Cédulas (separadas por coma):", height=100, placeholder="Ej: 13465979, 87654321")

if st.button("Obtener Anexos y Links"):
    if not cedulas_input:
        st.warning("Ingrese al menos una cédula.")
    else:
        cedulas_list = [c.strip() for c in cedulas_input.split(',') if c.strip()]
        if not cedulas_list:
            st.warning("No se encontraron cédulas válidas.")
        else:
            st.info(f"Iniciando proceso para: {', '.join(cedulas_list)}")
            items_to_process = []
            original_links_display = {}
            with st.spinner("Autenticando..."):
                token = get_api_token(USERNAME, PASSWORD)

            if token:
                # --- 1. Recopilación ---
                progress_bar_cedulas = st.progress(0)
                status_text = st.empty()
                status_text.info("Fase 1: Recopilando información...")
                total_cedulas = len(cedulas_list)
                for i, cedula in enumerate(cedulas_list):
                    current_step_text = f"Cédula: {cedula} ({i+1}/{total_cedulas})"
                    status_text.info(f"{current_step_text} - Buscando órdenes...")
                    original_links_display[cedula] = []
                    orders = get_orders_for_cedula(token, cedula)
                    if orders:
                        st.write(f"**{current_step_text}**: {len(orders)} orden(es) encontrada(s).")
                        for order_idx, order in enumerate(orders):
                            order_id = order.get("id")
                            if not order_id:
                                continue
                            status_text.info(f"{current_step_text} - Info Orden ID: {order_id} ({order_idx+1}/{len(orders)})...")
                            attachments, links = get_order_details_and_attachments(token, order_id)
                            for att in attachments:
                                att["cedula"] = cedula
                                items_to_process.append(att)
                            if links:
                                original_links_display[cedula].append({"order_id": order_id, "links": links})
                                for link in links:
                                    link["cedula"] = cedula
                                    items_to_process.append(link)
                    else:
                        st.write(f"{current_step_text}: No se encontraron órdenes.")
                    progress_bar_cedulas.progress((i + 1) / total_cedulas)
                status_text.info("Fase 1: Recopilación completada.")
                progress_bar_cedulas.progress(1.0)

                # --- 2. Procesamiento (Descarga/Conversión/Compresión) ---
                if items_to_process:
                    total_items = len(items_to_process)
                    st.info(f"Fase 2: Procesando {total_items} elementos...")
                    zip_buffer = io.BytesIO()
                    processed_count = 0
                    error_count = 0
                    file_sequence = {ced: 0 for ced in cedulas_list} # Secuencia unificada

                    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                        download_progress = st.progress(0)
                        for idx, item in enumerate(items_to_process):
                            cedula_item = item["cedula"]
                            item_type = item["type"]
                            success = False
                            status_prefix = f"Procesando ({idx+1}/{total_items})"
                            # Incrementar secuencia para CADA item
                            file_sequence[cedula_item] += 1
                            current_sequence = file_sequence[cedula_item]
                            zip_folder = cedula_item # Carpeta

                            if item_type == "attachment":
                                original_filename = item['file_name']
                                download_url = item['download_url']
                                try:
                                    extension = os.path.splitext(original_filename)[1].lower() or ".file"
                                except Exception:
                                    extension = ".file"
                                # Usar secuencia unificada
                                zip_path = f"{zip_folder}/{cedula_item}-{current_sequence}{extension}"
                                status_text.info(f"{status_prefix}: [Anexo] {original_filename} -> {zip_path}")
                                success = download_file_to_zip(token, download_url, zipf, zip_path)
                            elif item_type == "link":
                                status_text.info(f"{status_prefix}: [Link] '{item['name']}'...")
                                # Construir el nombre base SIN extensión
                                base_zip_path = f"{zip_folder}/{cedula_item}-{current_sequence}"
                                # Pasar el nombre base a la función de procesamiento
                                success = process_link_item(token, item, zipf, base_zip_path)

                            if success:
                                processed_count += 1
                            else:
                                error_count += 1
                            download_progress.progress((idx + 1) / total_items)

                    status_text.success("Fase 2: Procesamiento completado.")
                    download_progress.progress(1.0)
                    zip_buffer.seek(0)

                    # --- 3. Descarga del ZIP ---
                    if processed_count > 0:
                        st.success(f"¡Éxito! Se procesaron {processed_count} elementos correctamente.")
                        if error_count > 0:
                            st.warning(f"Hubo errores procesando {error_count} elementos.")
                        st.download_button(
                            label=f"Descargar {processed_count} Archivos (ZIP)",
                            data=zip_buffer,
                            file_name=f"sugos_export_{'_'.join(cedulas_list)}.zip",
                            mime="application/zip"
                        )
                    else:
                        st.warning("No se pudo procesar exitosamente ningún elemento.")
                else:
                    st.info("No se encontró ningún anexo o link para procesar.")

                # --- 4. Mostrar Links Originales ---
                st.subheader("Links Originales Encontrados (referencia):")
                links_found_flag = False
                if original_links_display:
                    for ced, order_data_list in original_links_display.items():
                        # Solo mostrar cédula si tiene al menos una orden con links
                        if any(od["links"] for od in order_data_list):
                            links_found_flag = True
                            st.markdown(f"--- \n**Cédula: {ced}**")
                            for od in order_data_list:
                                if od["links"]:
                                    st.markdown(f"**Orden: {od['order_id']}**")
                                    for link in od["links"]:
                                        st.markdown(f"- [{link['name']}]({link['url']})")
                if not links_found_flag:
                    st.info("No se encontraron links asociados.")
            else:
                st.error("Fallo en la autenticación.")
# --- Pie de página ---
st.markdown("---")
st.caption("CRM SUGOS Downloader v0.0.1")