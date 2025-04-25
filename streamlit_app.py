# ... (Importaciones iguales a v10.5/v10.6) ...
import streamlit as st
import requests
import os
import zipfile
import io
from urllib.parse import urljoin, urlparse, urlencode
import re
import datetime
from xhtml2pdf import pisa
from bs4 import BeautifulSoup

# --- Configuración API ---
# (Sin cambios)

# --- Funciones de Ayuda ---
# ... (Todas las funciones auxiliares: get_api_token, get_orders_for_cedula,
#      get_order_details_and_attachments, sanitize_filename, download_file_to_zip,
#      process_link_item permanecen IGUALES que en la v10.5) ...
def get_api_token(api_username, api_password, config):
    api_base_url = config.get('api_base_url');
    if not api_base_url: st.error("Error: 'api_base_url' no definida en config."); return None
    login_url = urljoin(api_base_url, "custom/apps/api.php?login")
    payload = {"username": api_username, "password": api_password}; headers = {'Content-Type': 'application/json'}
    try:
        response = requests.post(login_url, json=payload, headers=headers, timeout=30); response.raise_for_status(); data = response.json()
        token = data.get("token") or data.get("access_token") or data.get("data", {}).get("token")
        if not token: st.error(f"Login fallido: No se pudo encontrar token."); return None
        st.success(f"Autenticación exitosa para {config.get('display_name', 'entorno')}.")
        return token
    except requests.exceptions.HTTPError as e:
        st.error(f"Error HTTP {e.response.status_code} en {login_url}.");
        if e.response.status_code == 401: st.error("Credenciales inválidas o no autorizadas.")
        else:
            try: st.error(f"Respuesta del servidor: {e.response.text}")
            except Exception: st.error("No se pudo obtener detalle de la respuesta.")
        return None
    except requests.exceptions.RequestException as e: st.error(f"Error de conexión durante autenticación a {login_url}: {e}"); return None
    except Exception as e: st.error(f"Error inesperado procesando login: {e}"); return None

def get_orders_for_cedula(token, cedula, config):
    api_base_url = config.get('api_base_url'); app_cfn_value = config.get('app_cfn')
    if not api_base_url or not app_cfn_value: st.error("Configuración inválida (falta api_base_url o app_cfn)."); return []
    consulta_url = urljoin(api_base_url, "custom/apps/api.php"); action_params = {"afn": "ordermanager", "cfn": app_cfn_value}
    target_url = f"{consulta_url}?{urlencode(action_params)}"
    payload = {"page-id": "existing-orders-page", "section-id": "existing-orders", "order-keyword": str(cedula).strip()}
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    try:
        response = requests.get(target_url, headers=headers, json=payload, timeout=60); response.raise_for_status(); data = response.json()
        if (data.get("status") == "OK" and "data" in data and "existing-orders" in data["data"]):
            records = data["data"]["existing-orders"].get("Records", []);
            if not records: return []
            return [{"id": rec.get("ID"), "tipo_servicio": rec.get("Carrier")} for rec in records if isinstance(rec, dict) and rec.get("ID")]
        else: return []
    except requests.exceptions.RequestException as e: st.error(f"[get_orders] Error HTTP GET para {cedula}: {e}"); return []
    except Exception as e: st.error(f"[get_orders] Error inesperado procesando {cedula}: {e}"); return []

def get_order_details_and_attachments(token, order_id, config):
    api_base_url = config.get('api_base_url'); download_base_url = config.get('download_base_url', api_base_url)
    app_cfn_value = config.get('app_cfn')
    if not api_base_url or not app_cfn_value: st.error("Configuración inválida (falta api_base_url o app_cfn)."); return [], []
    consulta_url = urljoin(api_base_url, "custom/apps/api.php"); action_params = {"afn": "ordermanager", "cfn": app_cfn_value}
    target_url = f"{consulta_url}?{urlencode(action_params)}"
    payload = {"page-id": "existing-orders-page", "section-id": "existing-orders", "order-id": str(order_id)}
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    attachments_info = []; links_info = []
    try:
        response = requests.get(target_url, headers=headers, json=payload, timeout=60); response.raise_for_status(); data = response.json()
        if data.get("status") == "OK" and "data" in data:
            order_details = data.get("data", {}).get("existing-orders", {})
            if not order_details or not isinstance(order_details, dict): return [], []
            # Anexos
            attachments_list = order_details.get("Attachments")
            if attachments_list and isinstance(attachments_list, list):
                for att in attachments_list:
                    if isinstance(att, dict):
                        f_id=att.get("ID"); f_name=att.get("FileName"); f_path=att.get("FolderPath")
                        if f_id and f_name and f_path:
                            f_url_name = f"{f_id}_{f_name}"; rel_path = f"{f_path.strip('/')}/{f_url_name}"
                            dl_url = urljoin(download_base_url, rel_path)
                            attachments_info.append({"type": "attachment", "id": f_id, "file_name": f_name, "download_url": dl_url})
            # Links
            links_list = order_details.get("Links")
            if links_list and isinstance(links_list, list):
                 for link in links_list:
                     if isinstance(link, dict):
                         l_name = link.get("Name"); rel_url = link.get("URL")
                         if l_name and rel_url:
                             abs_url = urljoin(api_base_url, rel_url.lstrip('/'))
                             links_info.append({"type": "link", "name": l_name, "url": abs_url, "order_id": order_id})
        return attachments_info, links_info
    except requests.exceptions.RequestException as e: st.error(f"[get_details] Error HTTP GET orden {order_id}: {e}"); return [], []
    except Exception as e: st.error(f"[get_details] Error inesperado procesando orden {order_id}: {e}"); return [], []

def sanitize_filename(filename):
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename); sanitized = re.sub(r'\s+', ' ', sanitized).strip(); return sanitized[:100]

def download_file_to_zip(token, download_url, zip_file_handle, zip_path):
    headers = {'Authorization': f'Bearer {token}'}
    try:
        response = requests.get(download_url, headers=headers, stream=True, timeout=180); response.raise_for_status()
        zip_file_handle.writestr(zip_path, response.content); return True
    except requests.exceptions.RequestException as e: st.error(f"Error descargando anexo {os.path.basename(zip_path)}: {e}"); return False
    except Exception as e: st.error(f"Error añadiendo anexo {os.path.basename(zip_path)} al zip: {e}"); return False

def process_link_item(token, link_info, zip_file_handle, base_zip_path, config):
    link_url = link_info["url"]; link_name = link_info["name"]
    api_base_url = config.get('api_base_url')
    if not api_base_url: st.error("Config inválida: falta api_base_url."); return False
    headers = {'Authorization': f'Bearer {token}'}
    try:
        response = requests.get(link_url, headers=headers, timeout=120); response.raise_for_status()
        content_type = response.headers.get('Content-Type', '').lower()
        # PDF Directo
        if 'application/pdf' in content_type:
            pdf_zip_path = base_zip_path + ".pdf"; zip_file_handle.writestr(pdf_zip_path, response.content); return True
        # HTML
        elif 'text/html' in content_type:
            html_content = response.content; soup = BeautifulSoup(html_content, 'html.parser'); iframe = soup.find('iframe')
            if iframe and iframe.get('src'): # Iframe encontrado
                iframe_src = iframe['src']; iframe_url = urljoin(api_base_url, iframe_src)
                try:
                    iframe_response = requests.get(iframe_url, headers=headers, timeout=120); iframe_response.raise_for_status()
                    iframe_content = iframe_response.content; pdf_buffer = io.BytesIO()
                    content_bytes = iframe_content if isinstance(iframe_content, bytes) else iframe_content.encode('utf-8')
                    pisa_status = pisa.CreatePDF(io.BytesIO(content_bytes), dest=pdf_buffer, encoding='utf-8')
                    if pisa_status.err:
                        st.error(f"Error convirtiendo iframe '{link_name}': {pisa_status.err}")
                        fb_path = base_zip_path + "_iframe.html"; st.warning(f"Guardando iframe HTML: {fb_path}")
                        zip_file_handle.writestr(fb_path, content_bytes); return False
                    pdf_buffer.seek(0); pdf_zip_path = base_zip_path + ".pdf"; zip_file_handle.writestr(pdf_zip_path, pdf_buffer.read()); return True
                except requests.exceptions.RequestException as e_iframe: st.error(f"Error descargando iframe '{link_name}': {e_iframe}"); return False
                except Exception as e_conv: st.error(f"Error procesando iframe '{link_name}': {e_conv}"); return False
            else: # Fallback HTML Principal
                st.warning(f"No iframe en '{link_name}'. Convirtiendo HTML principal.")
                pdf_buffer = io.BytesIO(); content_bytes = html_content if isinstance(html_content, bytes) else html_content.encode('utf-8')
                pisa_status = pisa.CreatePDF(io.BytesIO(content_bytes), dest=pdf_buffer, encoding='utf-8')
                if pisa_status.err:
                    st.error(f"Error convirtiendo HTML principal '{link_name}': {pisa_status.err}")
                    fb_path = base_zip_path + "_main.html"; st.warning(f"Guardando HTML principal: {fb_path}")
                    zip_file_handle.writestr(fb_path, content_bytes); return False
                pdf_buffer.seek(0); pdf_zip_path = base_zip_path + ".pdf"; zip_file_handle.writestr(pdf_zip_path, pdf_buffer.read()); return True
        else: # Tipo Desconocido
            st.warning(f"Tipo desconocido '{content_type}' para '{link_name}'. Guardando binario.")
            fallback_zip_path = base_zip_path + ".bin"; zip_file_handle.writestr(fallback_zip_path, response.content); return True
    except requests.exceptions.RequestException as e: st.error(f"Error procesando link '{link_name}': {e}"); return False
    except Exception as e: st.error(f"Error inesperado procesando link '{link_name}': {e}"); return False

# --- Interfaz de Streamlit ---

st.set_page_config(page_title="SUGOS Downloader", layout="wide")
st.title("CRM SUGOS Downloader v0.0.2") # <-- v10.9
st.markdown("""
**Seleccione Entorno/Cliente**, ingrese **credenciales API** y **cédulas**.
Las cédulas duplicadas se procesan una sola vez. El campo de cédulas se limpiará tras generar el ZIP.
""")

# --- Inicializar Estado si no existe ---
# 'cedulas' se inicializa automáticamente por el widget
if 'run_processed' not in st.session_state:
    st.session_state.run_processed = False # Flag para saber si el procesamiento anterior terminó

# --- Cargar Configuraciones de Entorno ---
selected_config = None
ENVIRONMENT_CONFIGS = {}
try:
    # ... (Lógica de carga de configs sin cambios) ...
    if hasattr(st.secrets, 'items'): all_secrets = st.secrets.items()
    else: all_secrets = [(key, st.secrets[key]) for key in st.secrets.keys()]
    for section_key, section_content in all_secrets:
        if isinstance(section_content, dict) and section_content.get('display_name') and section_content.get('api_base_url'):
            ENVIRONMENT_CONFIGS[section_content['display_name']] = section_key
    if not ENVIRONMENT_CONFIGS: st.sidebar.error("Error: No config. válidas en secrets.toml."); st.stop()
    sorted_display_names = sorted(ENVIRONMENT_CONFIGS.keys())
    selected_display_name = st.sidebar.selectbox("Seleccionar Entorno/Cliente:", options=sorted_display_names, index=0, key="env_select")
    selected_secret_key = ENVIRONMENT_CONFIGS[selected_display_name]; selected_config = st.secrets[selected_secret_key]
    # st.sidebar.caption(f"API URL: {selected_config.get('api_base_url', 'N/A')}")
    # st.sidebar.caption(f"Download URL: {selected_config.get('download_base_url', 'N/A')}")
    # st.sidebar.caption(f"App CFN: {selected_config.get('app_cfn', 'N/A')}")
except AttributeError: st.sidebar.error("Error: Fallo al acceder a st.secrets."); st.stop()
except Exception as e: st.sidebar.error(f"Error crítico cargando config: {e}"); st.stop()

# --- Inputs para Credenciales ---
st.sidebar.header("Credenciales API"); default_user = st.secrets.get("api_credentials", {}).get("username", ""); default_pass = st.secrets.get("api_credentials", {}).get("password", "")
input_api_username = st.sidebar.text_input("Usuario API", value=default_user, key="api_user")
input_api_password = st.sidebar.text_input("Contraseña API", value=default_pass, type="password", key="api_pass")
st.sidebar.caption("Credenciales para el entorno seleccionado.")

# --- Entrada de Cédulas ---
# >>> LIMPIAR SI LA EJECUCIÓN ANTERIOR TERMINÓ <<<
if st.session_state.run_processed:
    st.session_state.cedulas = "" # Limpiar valor
    st.session_state.run_processed = False # Resetear flag para la próxima ejecución

cedulas_input_value = st.text_area(
    "Cédulas (separadas por coma):", height=100,
    placeholder="Ej: 13465979, 87654321, 13465979", key="cedulas"
)


# --- Función Callback para el Botón ---
def handle_submit():
    # Este callback se ejecuta ANTES que el resto del script en el rerun
    # por ahora no necesitamos hacer nada especial aquí, pero lo dejamos
    # como estructura por si se necesita más adelante
    # Podríamos resetear flags aquí si fuera complejo, pero no parece necesario aún
    pass

# --- Botón de Acción con Callback ---
process_button_pressed = st.button(
    "Obtener Anexos y Links",
    key="submit_button",
    on_click=handle_submit # Asociar el callback
)

if process_button_pressed:
    # Esta parte se ejecuta DESPUÉS del callback handle_submit en el rerun

    # Validar entradas...
    if not selected_config: st.error("Error crítico: No config seleccionada."); st.stop()
    if not input_api_username or not input_api_password: st.warning("⚠️ Ingrese Usuario y Contraseña API."); st.stop()
    # Leer del estado de sesión, que es la fuente de verdad para el widget
    cedulas_current_value = st.session_state.cedulas
    if not cedulas_current_value: st.warning("⚠️ Ingrese al menos una cédula."); st.stop() # Usar el valor leído

    initial_cedulas_list = [c.strip() for c in cedulas_current_value.split(',') if c.strip()]
    if not initial_cedulas_list: st.warning("⚠️ No cédulas válidas tras procesar entrada."); st.stop()
    unique_cedulas = list(dict.fromkeys(initial_cedulas_list))
    if len(initial_cedulas_list) > len(unique_cedulas):
        duplicates_removed = len(initial_cedulas_list) - len(unique_cedulas)
        st.info(f"ℹ️ Nota: Se eliminaron {duplicates_removed} cédulas duplicadas.")

    st.info(f"Iniciando para {len(unique_cedulas)} cédula(s) única(s): {', '.join(unique_cedulas)}")
    items_to_process = []; original_links_display = {}; file_sequence = {ced: 0 for ced in unique_cedulas}

    with st.spinner("Autenticando..."): token = get_api_token(input_api_username, input_api_password, selected_config)

    if token:
        # --- 1. Recopilación ---
        # ... (sin cambios) ...
        progress_bar_cedulas = st.progress(0); status_text = st.empty(); status_text.info("Fase 1: Recopilando info...")
        total_unique_cedulas = len(unique_cedulas)
        for i, cedula in enumerate(unique_cedulas):
            current_step_text = f"Cédula: {cedula} ({i+1}/{total_unique_cedulas})"
            status_text.info(f"{current_step_text} - Buscando órdenes...")
            original_links_display[cedula] = []
            orders = get_orders_for_cedula(token, cedula, selected_config)
            if orders:
                for order_idx, order in enumerate(orders):
                    order_id = order.get("id");
                    if not order_id: continue
                    status_text.info(f"{current_step_text} - Info Orden ID: {order_id} ({order_idx+1}/{len(orders)})...")
                    attachments, links = get_order_details_and_attachments(token, order_id, selected_config)
                    for att in attachments: att["cedula"] = cedula; items_to_process.append(att)
                    if links:
                        original_links_display[cedula].append({"order_id": order_id, "links": links})
                        for link in links: link["cedula"] = cedula; items_to_process.append(link)
            progress_bar_cedulas.progress((i + 1) / total_unique_cedulas)
        status_text.info("Fase 1: Recopilación completada."); progress_bar_cedulas.progress(1.0)

        # --- 2. Procesamiento ---
        if items_to_process:
            # ... (sin cambios en la lógica de procesamiento) ...
            total_items = len(items_to_process); st.info(f"Fase 2: Procesando {total_items} elementos...")
            zip_buffer = io.BytesIO(); processed_count = 0; error_count = 0
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                download_progress = st.progress(0)
                for idx, item in enumerate(items_to_process):
                    cedula_item = item["cedula"]; item_type = item["type"]; success = False
                    status_prefix = f"Procesando ({idx+1}/{total_items})"
                    file_sequence[cedula_item] += 1; current_sequence = file_sequence[cedula_item]
                    zip_folder = cedula_item
                    if item_type == "attachment":
                        original_filename = item['file_name']; download_url = item['download_url']
                        try: file_name, extension = os.path.splitext(original_filename); extension = extension.lower() or ".file"
                        except Exception: extension = ".file"
                        zip_path = f"{zip_folder}/{cedula_item}-{current_sequence}{extension}"
                        success = download_file_to_zip(token, download_url, zipf, zip_path)
                    elif item_type == "link":
                        base_zip_path = f"{zip_folder}/{cedula_item}-{current_sequence}"
                        success = process_link_item(token, item, zipf, base_zip_path, selected_config)
                    if success: processed_count += 1
                    else: error_count += 1
                    download_progress.progress((idx + 1) / total_items)
            status_text.success("Fase 2: Procesamiento completado."); download_progress.progress(1.0); zip_buffer.seek(0)

            # --- 3. Descarga del ZIP ---
            if processed_count > 0:
                 st.success(f"¡Éxito! {processed_count} elementos procesados.")
                 if error_count > 0: st.warning(f"{error_count} elementos tuvieron errores.")
                 timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                 env_tag = selected_secret_key.replace("_", "-")
                 zip_filename = f"sugos_export_{env_tag}_{timestamp}.zip"

                 st.download_button(
                     label=f"Descargar {processed_count} Archivos (ZIP)",
                     data=zip_buffer,
                     file_name=zip_filename,
                     mime="application/zip"
                 )

                 # >>> ESTABLECER FLAG PARA LIMPIAR EN LA PRÓXIMA EJECUCIÓN <<<
                 st.session_state.run_processed = True
                 # No forzamos rerun, dejamos que Streamlit siga su flujo natural.
                 # La limpieza ocurrirá la próxima vez que el script se ejecute desde arriba.

            else:
                st.warning("No se pudo procesar exitosamente ningún elemento.")
                st.session_state.run_processed = False # No limpiar si no hubo éxito
        else:
            st.info("No se encontró ningún anexo o link para procesar.")
            st.session_state.run_processed = False # No limpiar si no hubo nada que procesar

        # --- 4. Mostrar Links Originales ---
        # ... (sin cambios) ...
        st.subheader("Links Originales Encontrados (referencia):")
        links_found_flag = False
        if original_links_display:
            for ced, order_data_list in original_links_display.items():
                if any(od["links"] for od in order_data_list):
                    links_found_flag = True; st.markdown(f"--- \n**Cédula: {ced}**")
                    for od in order_data_list:
                          if od["links"]:
                              st.markdown(f"**Orden: {od['order_id']}**")
                              for link in od["links"]: st.markdown(f"- [{link['name']}]({link['url']})")
        if not links_found_flag: st.info("No se encontraron links asociados.")
    # else: # Fallo de token manejado

# --- Pie de página ---
st.markdown("---")
st.caption("CRM SUGOS Downloader v0.0.2")