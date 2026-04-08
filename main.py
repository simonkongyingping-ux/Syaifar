import flet as ft
from supabase import create_client, Client
from datetime import datetime, timedelta, timezone
import traceback 
import re
import mimetypes 
import uuid
import requests 
import json
import os
import threading
import time 
from PIL import Image
import io

# --- SAFE PUBLIC KEYS ---
SUPABASE_URL = "https://qqxaujdifamluzlhixvv.supabase.co".strip()
SUPABASE_KEY = "sb_publishable_lVx4h2kOKOB6_zenbJRH_g_GNCt5TgX".strip()
CF_PUBLIC_URL = "https://pub-32ddff17a392469a9df219f69d896722.r2.dev"

APP_VERSION = "v3.3"

# --- STATUS CONFIG ---
STATUSES_NEW = [
    (0, "0% - Job Created"), (1, "10% - Chassis Fab Started"), (2, "30% - Body Fab Started"),
    (3, "50% - Painting Started"), (4, "80% - Fittings Started"), (5, "90% - PUS / JPJ"),
    (6, "100% - Ready For Delivery"), (7, "Delivered"), (8, "Closed / Archived"), (9, "Deleted / Trash"),
]

STATUSES_USED = [
    (0, "0% - Job Created"), (3, "50% - Work In Progress"), (6, "100% - Ready For Delivery"),
    (7, "Delivered"), (8, "Closed / Archived"), (9, "Deleted / Trash"),
]

STATUS_DICT = {idx: label for idx, label in STATUSES_NEW}
STATUS_DICT[3] = "50% - Painting / WIP" 

SEARCHABLE_FIELDS = {
    "job_code": "Job Code", "memo_no": "Memo No", "invoice_no": "Invoice No",
    "do_no": "DO No", "customer": "Customer Name", "supervisor": "PIC / Supervisor",
    "chassis_no": "Chassis No", "vehicle_no": "Vehicle Reg No", "billed_by": "Billed By",
    "trailer_type": "Trailer Type", "summary": "Job Summary"
}

def get_mys_iso():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()

# --- IMAGE COMPRESSION LOGIC ---
def get_file_bytes_compressed(file_path):
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type and mime_type.startswith('image'):
        try:
            img = Image.open(file_path)
            if img.mode in ("RGBA", "P"): img = img.convert("RGB")
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='JPEG', quality=60, optimize=True)
            return img_byte_arr.getvalue()
        except Exception as e:
            print(f"Compression skipped due to error: {e}")
    with open(file_path, "rb") as f:
        return f.read()

# --- DATABASE MANAGER ---
class DbManager:
    def __init__(self):
        if SUPABASE_URL and SUPABASE_KEY: self.client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        else: self.client = None

    def fetch_directory(self):
        if not self.client: return []
        try:
            res = self.client.table("user_directory").select("*").neq("display_name", "Guest").order("sort_order").execute()
            return res.data
        except Exception: return []

    def login(self, email, password):
        if not self.client: return False, "DB Connection Failed"
        try:
            res = self.client.auth.sign_in_with_password({"email": email, "password": password})
            if res.session:
                if email.lower() == "guest@kanban.admin": return True, "guest"
                return True, "admin" 
        except Exception: return False, "Invalid Password"

    def fetch_jobs(self, category="new", status_filter=None, search_term=None, search_fields=None):
        if not self.client: return []
        try:
            query = self.client.table("memo_system").select("*").eq("category", category)
            if search_term and search_fields:
                if len(search_fields) > 0:
                    or_str = ",".join([f"{field}.ilike.%{search_term}%" for field in search_fields])
                    query = query.or_(or_str)
                else: return []
            else:
                if status_filter in [8, 9]: query = query.eq("status_idx", status_filter)
                else:
                    query = query.neq("status_idx", 8).neq("status_idx", 9)
                    if status_filter is not None: query = query.eq("status_idx", status_filter)
            res = query.order("created_at", desc=True).execute()
            return res.data
        except Exception: return []

    def fetch_single_job(self, job_code):
        if not self.client: return None
        try:
            res = self.client.table("memo_system").select("*").eq("job_code", job_code).execute()
            if res.data: return res.data[0] 
            return None
        except Exception: return None

    def fetch_history(self, job_code=None, limit=50): 
        if not self.client: return []
        try:
            query = self.client.table("job_history").select("*").order("changed_at", desc=True)
            if job_code: query = query.eq("job_code", job_code)
            return query.limit(limit).execute().data
        except Exception as e: 
            print(f"DATABASE FETCH ERROR: {str(e)}") 
            return [{"changed_by": "SYSTEM ALERT", "job_code": "DB ERROR", "details": f"Fetch failed: {str(e)}", "changed_at": get_mys_iso(), "new_status": 0}]

    def create_job(self, data, user):
        if not self.client: return False, "No DB Connection"
        try:
            data["created_at"] = data["updated_at"] = get_mys_iso()
            res = self.client.table("memo_system").insert(data).execute()
            if res.data:
                self.log_history(res.data[0]['id'], data['job_code'], None, 0, user, "Job Created")
                return True, None
            return False, "Insert failed"
        except Exception as e: return False, str(e)

    def update_job(self, job_id, new_data, user, job_code, old_status=None, new_status=None):
        if not self.client: return False, "No DB Connection"
        try:
            current_res = self.client.table("memo_system").select("*").eq("id", job_id).execute()
            if not current_res.data: return False, "Job not found"
            old_data = current_res.data[0]

            new_data["updated_at"] = get_mys_iso()
            self.client.table("memo_system").update(new_data).eq("id", job_id).execute()
            
            log_msg = None
            if old_status is not None and new_status is not None and old_status != new_status:
                log_msg = f"{STATUS_DICT.get(old_status, '?').split(' - ')[0]} -> {STATUS_DICT.get(new_status, '?').split(' - ')[0]}"
            else:
                core_fields = ["customer", "price_text", "memo_no", "invoice_no", "do_no", "billed_by", "flagged", "payment_type", "chassis_no", "vehicle_no", "supervisor", "trailer_type", "summary"]
                notes_fields = ["notes", "price_breakdown"]
                
                core_changed = any(str(old_data.get(f,'')) != str(new_data.get(f,'')) for f in core_fields if f in new_data)
                notes_changed = any(str(old_data.get(f,'')) != str(new_data.get(f,'')) for f in notes_fields if f in new_data)

                if core_changed: log_msg = "Job details updated"
                elif notes_changed: log_msg = "Notes & costing updated"

            if log_msg: 
                self.log_history(job_id, new_data.get("job_code", job_code), old_status if old_status is not None else old_data['status_idx'], new_status if new_status is not None else old_data['status_idx'], user, log_msg)
            return True, None
        except Exception as e: return False, str(e)
    
    def hard_delete_job(self, job_id, job_code, user): 
        if not self.client: return False, "No DB Connection"
        try:
            attachments = self.client.table("job_attachments").select("file_link").eq("job_id", job_id).execute().data
            
            if attachments:
                for att in attachments:
                    link = att.get("file_link")
                    if link:
                        try:
                            self.client.functions.invoke("bright-action", invoke_options={"body": {"file_link": link}})
                        except Exception as ex:
                            print(f"Failed to wipe {link} from Cloudflare: {ex}")

            self.client.table("job_receipts").delete().eq("job_id", job_id).execute()
            self.client.table("job_attachments").delete().eq("job_id", job_id).execute()
            self.client.table("memo_system").delete().eq("id", job_id).execute()
            
            self.log_history(job_id, job_code, 9, 9, user, "🚨 JOB AND ALL CLOUD FILES PERMANENTLY WIPED")
            return True, None
        except Exception as e: return False, str(e)

    def log_history(self, job_id, job_code, old_s, new_s, user, details_text=""):
        if not self.client: return False, "No Client"
        try:
            log = {"job_id": job_id, "job_code": job_code, "old_status": old_s if old_s is not None else -1, "new_status": new_s if new_s is not None else 0, "changed_by": user, "changed_at": get_mys_iso(), "details": details_text}
            self.client.table("job_history").insert(log).execute()
            return True, None
        except Exception as e: 
            print(f"DATABASE INSERT ERROR: {str(e)}")
            return False, str(e)

    def fetch_receipts(self, job_id):
        if not self.client: return []
        try: return self.client.table("job_receipts").select("*").eq("job_id", job_id).order("payment_date", desc=True).execute().data
        except Exception: return []

    def add_receipt(self, data, current_total):
        if not self.client: return False, "No DB Connection"
        try:
            self.client.table("job_receipts").insert(data).execute()
            all_recs = self.client.table("job_receipts").select("amount_paid").eq("job_id", data["job_id"]).execute()
            new_total = sum(float(r.get('amount_paid', 0)) for r in all_recs.data)
            self.client.table("memo_system").update({"total_paid": new_total}).eq("id", data["job_id"]).execute()
            return True, new_total
        except Exception as e: return False, str(e)

    def update_receipt(self, receipt_id, new_data, job_id):
        if not self.client: return False, "No DB Connection"
        try:
            self.client.table("job_receipts").update(new_data).eq("id", receipt_id).execute()
            all_recs = self.client.table("job_receipts").select("amount_paid").eq("job_id", job_id).execute()
            new_total = sum(float(r.get('amount_paid', 0)) for r in all_recs.data)
            self.client.table("memo_system").update({"total_paid": new_total}).eq("id", job_id).execute()
            return True, new_total
        except Exception as e: return False, str(e)

    def delete_receipt(self, receipt_id, job_id):
        if not self.client: return False, "No DB Connection"
        try:
            self.client.table("job_receipts").delete().eq("id", receipt_id).execute()
            all_recs = self.client.table("job_receipts").select("amount_paid").eq("job_id", job_id).execute()
            new_total = sum(float(r.get('amount_paid', 0)) for r in all_recs.data)
            self.client.table("memo_system").update({"total_paid": new_total}).eq("id", job_id).execute()
            return True, new_total
        except Exception as e: return False, str(e)

    def fetch_attachments(self, job_id):
        if not self.client: return []
        try: return self.client.table("job_attachments").select("*").eq("job_id", job_id).order("uploaded_at", desc=True).execute().data
        except Exception: return []

    def add_attachment(self, data):
        if not self.client: return False, "No DB Connection"
        try:
            self.client.table("job_attachments").insert(data).execute()
            return True, None
        except Exception as e: return False, str(e)

    def rename_attachment(self, att_id, new_name):
        if not self.client: return False, "No DB Connection"
        try:
            self.client.table("job_attachments").update({"file_name": new_name}).eq("id", att_id).execute()
            return True, None
        except Exception as e: return False, str(e)

    def delete_attachment(self, att_id):
        if not self.client: return False, "No DB Connection"
        try:
            self.client.table("job_attachments").delete().eq("id", att_id).execute()
            return True, None
        except Exception as e: return False, str(e)

db = DbManager()

# --- MAIN APP ---
def main(page: ft.Page):
    is_mobile = page.platform in [ft.PagePlatform.ANDROID, ft.PagePlatform.IOS]

    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 5 if is_mobile else 0
    page.title = f"Syaifar's Kanban {APP_VERSION}"
    if not is_mobile: page.window.min_width = 1000; page.window.min_height = 700; page.window.center()

    global_file_picker = ft.FilePicker()
    page.overlay.append(global_file_picker)

    active_date_field = [None]
    def on_date_changed(e):
        if active_date_field[0] and e.control.value:
            fixed_date = e.control.value + timedelta(hours=8)
            active_date_field[0].value = fixed_date.strftime("%Y-%m-%d")
            page.update()

    global_date_picker = ft.DatePicker(
        first_date=datetime(2020, 1, 1),
        last_date=datetime(2040, 12, 31),
        on_change=on_date_changed
    )
    page.overlay.append(global_date_picker)

    def open_date_picker(e, text_field):
        active_date_field[0] = text_field
        try: 
            clean_date = datetime.strptime(text_field.value, "%Y-%m-%d")
            global_date_picker.value = clean_date
        except: 
            global_date_picker.value = datetime.now()
            
        page.open(global_date_picker)
        page.update()

    state = {
        "user": "", "role": "", "category": "new", "last_view_type": "overview", 
        "last_status_idx": None, "last_search_term": "", "active_search_fields": list(SEARCHABLE_FIELDS.keys()), 
        "last_local_filter": "", "history_filter_job": None, 
        "feed_limit": 50, "feed_user_filter": "", 
        "scroll_pos": 0.0, "in_details": False, 
        "pre_history_view_type": None, "pre_history_in_details": False, "pre_history_job": None
    }

    def handle_back_button(e=None):
        try:
            if page.drawer and page.drawer.open: page.drawer.open = False; page.update(); return True
            
            if state["last_view_type"] == "history" and state.get("history_filter_job"):
                was_in_details = state.get("pre_history_in_details", False)
                job_to_return = state.get("pre_history_job")
                state["last_view_type"] = state.get("pre_history_view_type", "overview")
                state["pre_history_job"] = None
                if was_in_details and job_to_return: show_job_details(job_to_return)
                else: state["in_details"] = False; reload_current_view()
                return True

            if state["in_details"]: state["in_details"] = False; reload_current_view(); page.update(); return True
            if state["user"] != "" and state["last_view_type"] != "overview": state["last_view_type"] = "overview"; load_job_list_view("Overview Dashboard"); page.update(); return True
            return False
        except Exception: return False

    page.on_back_button = handle_back_button
    def on_keyboard(e: ft.KeyboardEvent):
        if e.key == "Escape": handle_back_button()
    page.on_keyboard_event = on_keyboard; page.update()

    def show_snack(msg, is_error=False):
        snack = ft.SnackBar(content=ft.Text(msg), bgcolor=ft.Colors.RED if is_error else ft.Colors.GREEN)
        page.open(snack)
        page.update()

    def safe_open_drawer(e): page.drawer.open = True; page.update()

    def view_specific_history(e, job): 
        state["pre_history_view_type"] = state["last_view_type"]
        state["pre_history_in_details"] = state["in_details"]
        state["pre_history_job"] = job
        state["last_view_type"] = "history"
        load_history_view(job['job_code'])

    def get_drawer():
        current_status_list = STATUSES_NEW if state["category"] == "new" else STATUSES_USED
        nav_controls = [
            ft.Container(height=20), ft.Text(f"  MODE: {state['category'].upper()}", weight="bold", size=16, color="red"), ft.Container(height=10),
            ft.NavigationDrawerDestination(icon=ft.Icons.CONSTRUCTION, label="New Construction"),
            ft.NavigationDrawerDestination(icon=ft.Icons.CAR_REPAIR, label="Used / Refurb"), ft.Divider(thickness=1),
            ft.NavigationDrawerDestination(icon=ft.Icons.DASHBOARD, label="Overview (Dashboard)"),
            ft.NavigationDrawerDestination(icon=ft.Icons.SEARCH, label="Global Search"),
            ft.NavigationDrawerDestination(icon=ft.Icons.NOTIFICATIONS_ACTIVE, label="Live Activity Feed"), ft.Divider(thickness=1),
        ]
        for idx, label in current_status_list:
            if idx < 8: nav_controls.append(ft.NavigationDrawerDestination(icon=ft.Icons.CIRCLE_OUTLINED, label=label))
        nav_controls.extend([
            ft.Divider(thickness=1), ft.NavigationDrawerDestination(icon=ft.Icons.ARCHIVE, label="Closed / Archived"),
            ft.NavigationDrawerDestination(icon=ft.Icons.DELETE_OUTLINE, label="Trash / Deleted"),
            ft.NavigationDrawerDestination(icon=ft.Icons.HISTORY, label="Global History"),
            ft.NavigationDrawerDestination(icon=ft.Icons.LOGOUT, label="Logout"),
            ft.Container(content=ft.Text(APP_VERSION, color="grey", size=12), padding=10)
        ])
        return ft.NavigationDrawer(controls=nav_controls, on_change=on_nav_change)

    def on_nav_change(e):
        try:
            idx = e.control.selected_index
            state["last_local_filter"] = ""; state["scroll_pos"] = 0.0; state["in_details"] = False; state["history_filter_job"] = None 
            current_status_list = STATUSES_NEW if state["category"] == "new" else STATUSES_USED
            status_count = len([s for s in current_status_list if s[0] < 8])

            if idx == 0: state["category"] = "new"; state["last_view_type"] = "overview"; page.drawer = get_drawer(); load_job_list_view("New Construction Dashboard")
            elif idx == 1: state["category"] = "used"; state["last_view_type"] = "overview"; page.drawer = get_drawer(); load_job_list_view("Used / Refurb Dashboard")
            elif idx == 2: state["last_view_type"] = "overview"; load_job_list_view("Overview Dashboard")
            elif idx == 3: show_search_view()
            elif idx == 4: state["last_view_type"] = "live_feed"; load_live_feed_view()
            elif 5 <= idx < (5 + status_count): 
                list_idx = idx - 5; status_idx = current_status_list[list_idx][0]
                state["last_view_type"] = "status"; state["last_status_idx"] = status_idx; load_job_list_view(STATUS_DICT.get(status_idx, "Status View"))
            elif idx == (5 + status_count): state["last_view_type"] = "archive"; state["last_status_idx"] = 8; load_job_list_view("Archived Jobs")
            elif idx == (5 + status_count + 1): state["last_view_type"] = "deleted"; state["last_status_idx"] = 9; load_job_list_view("Trash / Deleted Jobs")
            elif idx == (5 + status_count + 2): state["last_view_type"] = "history"; load_history_view()
            elif idx == (5 + status_count + 3): state["user"] = ""; show_login(); return

            if page.drawer: page.drawer.open = False
            page.update()
        except Exception as ex: print(f"Nav Error: {ex}")

    def warm_up_servers():
        def ping_worker():
            try:
                db.client.functions.invoke("clever-action", invoke_options={"body": {"action": "ping"}})
                db.client.functions.invoke("bright-action", invoke_options={"body": {"action": "ping"}})
            except Exception as e: print(f"Warm up failed: {e}")
        t = threading.Thread(target=ping_worker, daemon=True)
        t.start()

    def start_auto_refresh():
        def refresh_worker():
            last_checked_id = None
            while True:
                time.sleep(60) 
                if state["user"] != "" and not state["in_details"]:
                    try:
                        latest_log = db.fetch_history(limit=1)
                        if latest_log and "SYSTEM ALERT" not in latest_log[0].get('changed_by', ''):
                            current_latest_id = latest_log[0].get('id')
                            if last_checked_id is None:
                                last_checked_id = current_latest_id
                            elif current_latest_id != last_checked_id:
                                last_checked_id = current_latest_id
                                snack = ft.SnackBar(
                                    content=ft.Row([ft.Icon(ft.Icons.NOTIFICATIONS_ACTIVE, color="white"), ft.Text("New updates available in the system.", color="white")]), 
                                    bgcolor=ft.Colors.BLUE_900, duration=6000, action="Refresh", action_color="yellow", on_action=lambda e: reload_current_view()
                                )
                                page.open(snack)
                                page.update()
                    except Exception as e: pass
        t = threading.Thread(target=refresh_worker, daemon=True)
        t.start()
        
    start_auto_refresh()

    def reload_current_view():
        view_type = state["last_view_type"]; state["in_details"] = False 
        if view_type == "overview": load_job_list_view(f"{state['category'].capitalize()} Dashboard")
        elif view_type == "status": load_job_list_view(STATUS_DICT.get(state["last_status_idx"], "Status View"))
        elif view_type == "archive": load_job_list_view("Archived Jobs")
        elif view_type == "deleted": load_job_list_view("Trash / Deleted Jobs")
        elif view_type == "search": load_job_list_view(f"Results: {state['last_search_term']}", is_global_search=True)
        elif view_type == "live_feed": load_live_feed_view()
        elif view_type == "history": load_history_view(state["history_filter_job"])
        else: load_job_list_view("Overview Dashboard")

    def show_login():
        page.clean(); page.appbar = None; page.drawer = None; state["in_details"] = False
        
        connection_status = "Connecting..."
        try: directory_data = db.fetch_directory(); connection_status = "Connected to Secure Database" if db.client else "Offline / Connection Error"
        except Exception: directory_data = []; connection_status = "Offline / Connection Error"

        user_mapping = {user['display_name']: user['auth_email'] for user in directory_data}
        dropdown_options = [ft.dropdown.Option(name) for name in user_mapping.keys()]
        if not dropdown_options: dropdown_options = [ft.dropdown.Option("No Users Found")]

        def attempt_login(e=None):
            if not user_dropdown.value or not pass_in.value: status_lbl.value = "Select a name and enter password."; status_lbl.color = "red"; page.update(); return
            login_btn.disabled = True
            status_lbl.value = "Checking with Security..."; status_lbl.color = "grey"; page.update()
            target_email = user_mapping.get(user_dropdown.value)
            success, role = db.login(target_email, pass_in.value)
            login_btn.disabled = False
            if success: 
                state["user"] = user_dropdown.value; 
                state["role"] = role; 
                warm_up_servers()
                page.drawer = get_drawer(); 
                reload_current_view() 
            else: status_lbl.value = role; status_lbl.color = "red"; page.update()

        user_dropdown = ft.Dropdown(label="Select Your Name", expand=True, options=dropdown_options, autofocus=True)
        pass_in = ft.TextField(label="Password", password=True, expand=True, on_submit=attempt_login)
        login_btn = ft.ElevatedButton("Login", on_click=attempt_login, bgcolor="blue", color="white")
        status_lbl = ft.Text(connection_status, color="grey")

        def guest_login_click(e):
            e.control.disabled = True
            status_lbl.value = "Logging in as Guest..."; status_lbl.color = "grey"; page.update()
            success, role = db.login("guest@kanban.admin", "guestview123")
            if success: 
                state["user"] = "Guest Visitor"; state["role"] = role; page.drawer = get_drawer()
                reload_current_view()
            else: e.control.disabled = False; status_lbl.value = "Guest account not set up in Auth!"; status_lbl.color = "red"; page.update()

        page.add(
            ft.Stack([
                ft.Container(
                    content=ft.Column([
                        ft.Icon(ft.Icons.DIRECTIONS_CAR, size=60, color="blue"), ft.Text("Syaifar's Kanban", size=24, weight="bold"),
                        ft.Text("Secure Job Management", size=16), ft.Divider(height=20, color="transparent"),
                        ft.Row([user_dropdown]), ft.Row([pass_in]), login_btn,
                        ft.Divider(height=10, color="transparent"), ft.TextButton("Login as Guest (View Only)", on_click=guest_login_click, icon=ft.Icons.REMOVE_RED_EYE), status_lbl
                    ], horizontal_alignment="center", alignment=ft.MainAxisAlignment.CENTER, scroll=ft.ScrollMode.ADAPTIVE),
                    alignment=ft.alignment.center, expand=True, bgcolor=ft.Colors.BLUE_50, padding=20
                ),
                ft.Container(content=ft.Text(APP_VERSION, color="grey", size=12, weight="bold"), bottom=10, left=10)
            ], expand=True)
        )

    def show_search_view():
        page.clean(); state["last_local_filter"] = ""; state["in_details"] = False
        
        def run_search(e=None):
            if not search_box.value: return
            state["last_view_type"] = "search"; state["last_search_term"] = search_box.value
            load_job_list_view(f"Results: {search_box.value}", is_global_search=True)

        search_box = ft.TextField(label="Search Database", expand=True, autofocus=True, on_submit=run_search)
        search_btn = ft.IconButton(icon=ft.Icons.SEARCH, icon_color="blue", tooltip="Click to Search", on_click=run_search)
        
        def on_filter_toggle(e):
            field_key = e.control.data; is_checked = e.control.value
            if is_checked and field_key not in state["active_search_fields"]: 
                state["active_search_fields"].append(field_key)
            elif not is_checked and field_key in state["active_search_fields"]: 
                state["active_search_fields"].remove(field_key)

        filter_checkboxes = [ft.Checkbox(label=friendly_name, value=(field_key in state["active_search_fields"]), data=field_key, on_change=on_filter_toggle) for field_key, friendly_name in SEARCHABLE_FIELDS.items()]
        
        filter_expander = ft.ExpansionTile(
            title=ft.Text("Refine Search (Check boxes to look in specific fields)", size=12, color="blue", italic=True), 
            controls=[ft.Container(content=ft.Row(filter_checkboxes, wrap=True), padding=10)], 
            collapsed_text_color="grey", 
            text_color="blue"
        )

        page.appbar = ft.AppBar(leading=ft.IconButton(ft.Icons.MENU, on_click=safe_open_drawer), title=ft.Text("Global Search"), bgcolor="blue", color="white")
        
        page.add(ft.Container(
            content=ft.Column([
                ft.Row([search_box, search_btn], spacing=5), 
                filter_expander,
                ft.Divider(),
                ft.Text("Type a keyword and press Enter to begin.", color="grey", size=12, italic=True)
            ], scroll=ft.ScrollMode.ADAPTIVE), 
            padding=10, 
            expand=True
        ))
        search_box.focus(); page.update()

    def load_live_feed_view():
        page.clean(); state["in_details"] = False; limit = state.get("feed_limit", 50)
        
        def change_limit(e): state["feed_limit"] = int(e.control.value); load_live_feed_view()
        def change_user_filter(e): state["feed_user_filter"] = e.control.value; load_live_feed_view()

        def on_feed_click(e, j_code):
            if j_code == "DB ERROR": return
            real_job_data = db.fetch_single_job(j_code)
            if real_job_data: show_job_details(real_job_data)
            else: show_snack(f"Could not open {j_code}. It has been permanently deleted.", is_error=True)

        dd_limit = ft.Dropdown(label="Event Limit", options=[
            ft.dropdown.Option("50", "Last 50 Events"),
            ft.dropdown.Option("200", "Last 200 Events"),
            ft.dropdown.Option("1000", "Last 1000 Events")
        ], value=str(limit), expand=True, on_change=change_limit)

        # Grab the master list of users from the database
        try: directory_data = db.fetch_directory()
        except Exception: directory_data = []
        
        # Create the dropdown options, starting with an "All Users" option
        user_options = [ft.dropdown.Option("", "All Users")] 
        for u in directory_data:
            user_options.append(ft.dropdown.Option(u['display_name']))

        dd_user_filter = ft.Dropdown(label="Filter by User", options=user_options, value=state.get("feed_user_filter", ""), on_change=change_user_filter, expand=True)
        page.appbar = ft.AppBar(leading=ft.IconButton(ft.Icons.MENU, on_click=safe_open_drawer), title=ft.Text("Live Activity Feed"), bgcolor="blue", color="white")
        
        history_logs = db.fetch_history(limit=limit) 
        feed_list = ft.ListView(expand=True, spacing=10, padding=15)
        
        if not history_logs: feed_list.controls.append(ft.Text("No recent activity.", color="grey", text_align="center"))
        else:
            u_filter = state.get("feed_user_filter", "").lower()
            render_count = 0

            for log in history_logs:
                try:
                    user = log.get('changed_by', 'System')
                    if u_filter and u_filter not in user.lower(): continue
                    render_count += 1
                    job_c = log.get('job_code', 'Unknown'); old_s = STATUS_DICT.get(log.get('old_status'), '?')
                    new_s_id = log.get('new_status')
                    if new_s_id == 8: new_s = "Closed"
                    elif new_s_id == 9: new_s = "Deleted"
                    else: new_s = STATUS_DICT.get(new_s_id, 'Unknown')

                    raw_time = str(log.get('changed_at', '')); time_str = raw_time.replace('T', ' ')[:16] if raw_time else ""
                    details = log.get('details', '')
                    if details: msg = details
                    elif log.get('old_status') == -1: msg = "Job Created"
                    elif log.get('old_status') != log.get('new_status'): msg = f"{old_s} -> {new_s}"
                    else: msg = "Edited"
                    
                    is_error = user == "SYSTEM ALERT"
                    is_deleted = new_s_id == 9 or "PERMANENTLY WIPED" in msg
                    bg_color = ft.Colors.RED_50 if (is_deleted or is_error) else ft.Colors.GREY_100
                    
                    feed_list.controls.append(ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.ERROR if is_error else (ft.Icons.HISTORY if not is_deleted else ft.Icons.WARNING), size=18, color=ft.Colors.RED if (is_deleted or is_error) else "blue"), 
                            ft.Text(f"{time_str} | ", size=14, color="grey", weight="bold"), 
                            ft.Text(f"{user} " + ("reported " if is_error else "updated "), size=14, color="grey"), 
                            ft.Text(f"{job_c}: ", size=14, weight="bold", color=ft.Colors.RED_900 if (is_deleted or is_error) else "black"), 
                            ft.Text(f"{msg}", size=14, color=ft.Colors.RED_900 if (is_deleted or is_error) else "black87")
                        ], vertical_alignment="center", wrap=True), 
                        bgcolor=bg_color, padding=15, border_radius=8, ink=not is_error, 
                        on_click=lambda e, jc=job_c: on_feed_click(e, jc)
                    ))
                except Exception as ex:
                    print(f"Skipped a corrupted log entry: {ex}")

            if render_count == 0: feed_list.controls.append(ft.Text("No activity matches this user filter.", color="grey", text_align="center"))

        # FIX: Removed 'wrap=True' from the row holding the dropdown and text filter!
        page.add(ft.Container(content=ft.Column([ft.Container(content=ft.Row([dd_limit, dd_user_filter]), padding=10), ft.Divider(height=1, color="grey"), feed_list], expand=True), expand=True))
        page.update()

    def load_job_list_view(title, is_global_search=False):
        page.clean(); state["in_details"] = False
        current_status_list = STATUSES_NEW if state["category"] == "new" else STATUSES_USED

        appbar_actions = []
        if state["role"] == "admin": appbar_actions.append(ft.IconButton(ft.Icons.ADD, on_click=lambda e: show_job_details(None), tooltip="Create New Job"))
        appbar_actions.append(ft.IconButton(ft.Icons.REFRESH, on_click=lambda e: reload_current_view(), tooltip="Refresh"))

        page.appbar = ft.AppBar(leading=ft.IconButton(ft.Icons.MENU, on_click=safe_open_drawer), title=ft.Text(title, size=16), bgcolor="blue" if state["category"] == "new" else "orange", color="white", actions=appbar_actions)

        if is_global_search: jobs = db.fetch_jobs(category=state["category"], search_term=state["last_search_term"], search_fields=state["active_search_fields"])
        elif state["last_view_type"] == "archive": jobs = db.fetch_jobs(category=state["category"], status_filter=8)
        elif state["last_view_type"] == "deleted": jobs = db.fetch_jobs(category=state["category"], status_filter=9)
        elif state["last_view_type"] == "status": jobs = db.fetch_jobs(category=state["category"], status_filter=state["last_status_idx"])
        else: jobs = db.fetch_jobs(category=state["category"], status_filter=None)

        if state["last_view_type"] == "overview":
            stats = {idx: {'total': 0, 'red': 0, 'orange': 0, 'yellow': 0} for idx, _ in current_status_list if idx < 8}
            for j in jobs:
                s_idx = j.get('status_idx', 0)
                if s_idx in stats:
                    stats[s_idx]['total'] += 1
                    flag_raw = j.get('flagged', 0)
                    flag_val = 1 if flag_raw is True else (0 if flag_raw is False else int(flag_raw) if str(flag_raw).isdigit() else 0)
                    if flag_val == 1: stats[s_idx]['red'] += 1
                    elif flag_val == 2: stats[s_idx]['orange'] += 1
                    elif flag_val == 3: stats[s_idx]['yellow'] += 1

            def open_status_view(idx):
                state["last_view_type"] = "status"; state["last_status_idx"] = idx; state["last_local_filter"] = ""; state["scroll_pos"] = 0.0; reload_current_view()

            dash_cols = 1; dash_ratio = 4.5 
            dashboard_col = ft.GridView(expand=True, runs_count=dash_cols, child_aspect_ratio=dash_ratio, spacing=10, run_spacing=10, padding=10)
            
            for idx, label in current_status_list:
                if idx >= 8: continue 
                total = stats.get(idx, {}).get('total', 0); red_count = stats.get(idx, {}).get('red', 0); org_count = stats.get(idx, {}).get('orange', 0); yel_count = stats.get(idx, {}).get('yellow', 0)
                flag_badges = []
                if red_count > 0: flag_badges.append(ft.Container(content=ft.Text(f"🚨 {red_count}", color="white", weight="bold", size=12), bgcolor=ft.Colors.RED, padding=ft.padding.symmetric(horizontal=8, vertical=4), border_radius=4))
                if org_count > 0: flag_badges.append(ft.Container(content=ft.Text(f"⚠️ {org_count}", color="black", weight="bold", size=12), bgcolor=ft.Colors.ORANGE_400, padding=ft.padding.symmetric(horizontal=8, vertical=4), border_radius=4))
                if yel_count > 0: flag_badges.append(ft.Container(content=ft.Text(f"⏳ {yel_count}", color="black", weight="bold", size=12), bgcolor=ft.Colors.YELLOW_300, padding=ft.padding.symmetric(horizontal=8, vertical=4), border_radius=4))
                
                if not flag_badges: flag_section = ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN_300, size=24)
                else: flag_section = ft.Row(flag_badges, spacing=5, alignment="end", wrap=True)
                
                card_content = ft.Container(content=ft.Row([ft.Column([ft.Text(label, weight="bold", size=16), ft.Text(f"{total} Active Jobs", color="grey", size=13)], alignment="center"), ft.Container(content=flag_section, alignment=ft.alignment.center_right, expand=True)], alignment="spaceBetween"), padding=20, on_click=lambda e, i=idx: open_status_view(i))
                dashboard_col.controls.append(ft.Card(content=card_content, color="white", elevation=2))

            page.add(ft.Container(content=ft.Column([ft.Container(content=ft.Text(f"Active {state['category'].capitalize()} Jobs: {len(jobs)}", size=20, weight="bold", color="blue"), padding=10), ft.Container(content=dashboard_col, expand=True)], expand=True), expand=True))
            return 

        def on_scroll_list(e: ft.OnScrollEvent): state["scroll_pos"] = e.pixels

        list_container = ft.ListView(expand=True, spacing=10, padding=10, on_scroll=on_scroll_list)

        def draw_cards(job_list):
            list_container.controls.clear()
            if not job_list: list_container.controls.append(ft.Text("No jobs found with those filters.", text_align="center", color="grey")); return

            for job in job_list:
                flag_raw = job.get('flagged', 0); flag_val = 1 if flag_raw is True else (0 if flag_raw is False else int(flag_raw) if str(flag_raw).isdigit() else 0)
                flag_text_ui = None
                if flag_val == 1: card_bg = ft.Colors.RED_500; text_color = "white"; sub_text_color = "white70"; icon_color = "white"; flag_text_ui = ft.Text("🚨 NO DEPOSIT & SUSPENDED", color="white", weight="bold", size=13)
                elif flag_val == 2: card_bg = ft.Colors.ORANGE_400; text_color = "black"; sub_text_color = "black54"; icon_color = "black87"; flag_text_ui = ft.Text("⚠️ NO DEPOSIT & WIP", color="black", weight="bold", size=13)
                elif flag_val == 3: card_bg = ft.Colors.YELLOW_300; text_color = "black"; sub_text_color = "black54"; icon_color = "black87"; flag_text_ui = ft.Text("⏳ FOR STANDBY PURPOSE", color="black", weight="bold", size=13)
                else: card_bg = "white"; text_color = "black"; sub_text_color = "grey"; icon_color = "blue"
                
                raw_created = str(job.get('created_at', '')); raw_updated = str(job.get('updated_at', ''))
                nice_created = raw_created.replace('T', ' ')[:16] if raw_created else ""; nice_updated = raw_updated.replace('T', ' ')[:16] if raw_updated else ""
                date_col_controls = [ft.Text(f"Cr: {nice_created}", size=11, color=sub_text_color)]
                if nice_updated and nice_updated != nice_created: date_col_controls.append(ft.Text(f"Upd: {nice_updated}", size=11, color=sub_text_color))

                card_content = ft.Container(
                    content=ft.Column([
                        ft.Row([ft.Text(job['job_code'], weight="bold", size=16, color=text_color), ft.Column(date_col_controls, alignment="end", spacing=0)], alignment="spaceBetween"),
                        flag_text_ui if flag_text_ui else ft.Container(height=0),
                        ft.Text(f"Memo: {job.get('memo_no') or '-'}", weight="bold", size=14, color=ft.Colors.BLUE_900 if flag_val>0 else ft.Colors.BLUE),
                        ft.Text(f"Billed by: {job.get('billed_by') or '-'}", size=12, color=sub_text_color),
                        ft.Text(f"Inv: {job.get('invoice_no') or '-'}  |  DO: {job.get('do_no') or '-'}", size=12, color=text_color),
                        ft.Text(f"Cust: {job.get('customer', '-')}", size=14, color=text_color),
                        ft.Text(f"Chassis: {job.get('chassis_no', '-')}  |  Plate: {job.get('vehicle_no', '-')}", size=12, weight="bold", color=text_color),
                        ft.Text(f"Type: {job.get('trailer_type', '-')}", size=12, color=sub_text_color),
                        ft.Row([ft.Text(f"Price: RM {job.get('price_text', '-')}", size=12, color=sub_text_color), ft.Text(f"Paid: RM {float(job.get('total_paid', 0)):.2f}", size=12, weight="bold", color="green" if float(job.get('total_paid', 0)) > 0 else "grey")], spacing=10),
                        ft.Text(f"PIC: {job.get('supervisor', '-')}", size=12, color=sub_text_color),
                        ft.Divider(height=5, color="transparent"),
                        ft.Text(f"{job.get('summary', '')}", size=12, italic=True, color=text_color, max_lines=3, overflow="ellipsis"),
                        ft.Row([
                            ft.IconButton(ft.Icons.HISTORY, icon_size=24, icon_color=icon_color, tooltip="View Job History", on_click=lambda e, j=job: view_specific_history(e, j)), 
                            ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color=icon_color), ft.Text(f"{STATUS_DICT.get(job['status_idx'], 'Unknown')}", size=12, color=icon_color)])
                        ], alignment="spaceBetween")
                    ], spacing=5), 
                    padding=15, on_click=lambda e, j=job: show_job_details(j)
                )
                list_container.controls.append(ft.Card(content=card_content, color=card_bg))
            page.update()

        top_bar = None
        if is_global_search:
            def run_search_trigger(e=None):
                new_term = search_input.value
                if not new_term: return
                state["last_search_term"] = new_term; load_job_list_view(f"Results: {new_term}", is_global_search=True)

            search_input = ft.TextField(value=state["last_search_term"], label="Search Database", hint_text="Keyword...", expand=True, height=50, text_size=14, content_padding=10, on_submit=run_search_trigger, autofocus=True)
            search_btn = ft.IconButton(icon=ft.Icons.SEARCH, icon_color="blue", tooltip="Click to Search", on_click=run_search_trigger)
            
            def on_filter_toggle(e):
                field_key = e.control.data; is_checked = e.control.value
                if is_checked and field_key not in state["active_search_fields"]: 
                    state["active_search_fields"].append(field_key)
                elif not is_checked and field_key in state["active_search_fields"]: 
                    state["active_search_fields"].remove(field_key)

            filter_checkboxes = [ft.Checkbox(label=friendly_name, value=(field_key in state["active_search_fields"]), data=field_key, on_change=on_filter_toggle) for field_key, friendly_name in SEARCHABLE_FIELDS.items()]
            
            filter_expander = ft.ExpansionTile(
                title=ft.Text("Refine Search (Check boxes to look in specific fields)", size=12, color="blue", italic=True), 
                controls=[ft.Container(content=ft.Row(filter_checkboxes, wrap=True), padding=10)], 
                collapsed_text_color="grey", 
                text_color="blue"
            )
            top_bar = ft.Column([ft.Row([search_input, search_btn], spacing=5), filter_expander])
            draw_cards(jobs)
        else:
            def run_local_filter(e):
                filter_text = e.control.value.lower(); state["last_local_filter"] = filter_text; visible_jobs = []
                if not filter_text: visible_jobs = jobs
                else:
                    for j in jobs:
                        full_text = f"{j['job_code']} {j.get('memo_no','')} {j.get('do_no','')} {j.get('invoice_no','')} {j.get('customer','')} {j.get('supervisor','')} {j.get('chassis_no','')} {j.get('vehicle_no','')} {j.get('summary','')}".lower()
                        if filter_text in full_text: visible_jobs.append(j)
                draw_cards(visible_jobs)

            top_bar = ft.TextField(value=state.get("last_local_filter", ""), hint_text=f"Filter these {len(jobs)} jobs...", prefix_icon=ft.Icons.FILTER_LIST, height=50, text_size=14, content_padding=10, on_change=run_local_filter)
            run_local_filter(ft.ControlEvent(target="", name="change", data=state.get("last_local_filter", ""), control=top_bar, page=page))

        page.add(ft.Container(content=ft.Column([ft.Container(content=top_bar, padding=10), list_container], spacing=0, expand=True), expand=True, alignment=ft.alignment.top_center))
        if state["scroll_pos"] > 0: list_container.scroll_to(offset=state["scroll_pos"], duration=0)
        
        if is_global_search: search_input.focus(); page.update()

    def show_job_details(job):
        page.clean(); state["in_details"] = True 

        is_new = job is None
        is_readonly = state["role"] != "admin"

        current_job_id = job['id'] if job else None

        flag_raw = job.get('flagged', 0) if job else 0
        flag_val = 1 if flag_raw is True else (0 if flag_raw is False else int(flag_raw) if str(flag_raw).isdigit() else 0)

        category_val = job.get('category', state["category"]) if job else state["category"]
        id_label = "Job Code" if category_val == "new" else "Unit ID / Reg No"
        code_val = job['job_code'] if job else f"{'JOB' if category_val=='new' else 'USED'}-{int(datetime.now(timezone.utc).timestamp())}"
        
        def responsive_row(controls): return ft.Column(controls, spacing=10)
        def close_any_dialog(dlg): page.close(dlg); page.update()

        t_code = ft.TextField(label=id_label, value=code_val, disabled=False, expand=True)
        t_memo = ft.TextField(label="Memo NO", value=job.get('memo_no','') if job else "", expand=True)
        t_billed = ft.TextField(label="Billed By", value=job.get('billed_by','') if job else "")
        t_invoice = ft.TextField(label="Invoice No", value=job.get('invoice_no','') if job else "", expand=True)
        t_do = ft.TextField(label="DO Number", value=job.get('do_no','') if job else "", expand=True)
        t_chassis = ft.TextField(label="Chassis Number (VIN)", value=job.get('chassis_no','') if job else "", expand=True)
        t_vehicle = ft.TextField(label="Vehicle Plate / Reg No", value=job.get('vehicle_no','') if job else "", expand=True)
        t_cust = ft.TextField(label="Customer", value=job.get('customer','') if job else "")
        t_pic = ft.TextField(label="PIC / Supervisor", value=job.get('supervisor','') if job else "")
        t_type = ft.TextField(label="Trailer/Unit Type", value=job.get('trailer_type','') if job else "")
        t_price = ft.TextField(label="Price (Total)", value=job.get('price_text','') if job else "", keyboard_type=ft.KeyboardType.NUMBER)
        t_summary = ft.TextField(label="Summary", value=job.get('summary','') if job else "")
        t_notes = ft.TextField(label="Production Notes", value=job.get('notes','') if job else "", multiline=True, min_lines=3)
        t_breakdown = ft.TextField(label="Price Breakdown / Costing", value=job.get('price_breakdown','') if job else "", multiline=True, min_lines=3)
        
        dd_flag = ft.Dropdown(label="Job Label / Priority", options=[ft.dropdown.Option("0", "Normal"), ft.dropdown.Option("1", "🚨 No deposit & suspended (RED)"), ft.dropdown.Option("2", "⚠️ No deposit & WIP (ORANGE)"), ft.dropdown.Option("3", "⏳ For standby purpose (YELLOW)")], value=str(flag_val))
        dd_payment_type = ft.Dropdown(label="Payment Type", options=[ft.dropdown.Option("Cash"), ft.dropdown.Option("Full Loan"), ft.dropdown.Option("Deposit & Balance")], value=job.get('payment_type', 'Cash') if job else "Cash")

        receipts_list = ft.Column(spacing=5)
        edit_state = {"receipt_id": None}

        dlg_edit_date = ft.TextField(label="Date (YYYY-MM-DD)", read_only=True, expand=True)
        btn_cal_edit = ft.IconButton(icon=ft.Icons.CALENDAR_MONTH, icon_size=24, on_click=lambda e: open_date_picker(e, dlg_edit_date))
        row_edit_date = ft.Row([dlg_edit_date, btn_cal_edit])
        
        dlg_edit_no = ft.TextField(label="Receipt Number")
        dlg_edit_amt = ft.TextField(label="Amount (RM)", keyboard_type=ft.KeyboardType.NUMBER) 

        def save_edited_receipt(e):
            if not dlg_edit_no.value or not dlg_edit_amt.value or not dlg_edit_date.value: show_snack("Please fill all receipt fields", is_error=True); return
            try: amt_val = float(dlg_edit_amt.value)
            except ValueError: show_snack("Amount must be a number", is_error=True); return

            e.control.disabled = True; page.update()

            db_ready_date = f"{dlg_edit_date.value}T12:00:00+08:00"
            new_data = {"receipt_no": dlg_edit_no.value, "amount_paid": amt_val, "payment_date": db_ready_date}
            
            ok, result = db.update_receipt(edit_state["receipt_id"], new_data, current_job_id)
            e.control.disabled = False
            if ok: 
                show_snack("Receipt Updated!")
                if job: 
                    job['total_paid'] = result
                    db.log_history(job['id'], code_val, job.get('status_idx',0), job.get('status_idx',0), state["user"], "Finance and payments updated")
                close_any_dialog(edit_rec_dialog)
                load_receipts()
            else: show_snack(f"Failed: {result}", is_error=True)

        edit_rec_dialog = ft.AlertDialog(title=ft.Text("Edit Receipt"), content=ft.Column([row_edit_date, dlg_edit_no, dlg_edit_amt], tight=True), actions=[ft.TextButton("Cancel", on_click=lambda e: close_any_dialog(edit_rec_dialog)), ft.ElevatedButton("Update", color="white", bgcolor="blue", on_click=save_edited_receipt)])

        def open_edit_receipt(r):
            if is_readonly: return
            edit_state["receipt_id"] = r['id']; dlg_edit_date.value = str(r.get('payment_date', ''))[:10]; dlg_edit_no.value = r.get('receipt_no', ''); dlg_edit_amt.value = str(r.get('amount_paid', '0'))
            page.open(edit_rec_dialog)
            page.update()

        del_rec_state = {"id": None}
        def execute_del_receipt(e):
            e.control.disabled = True; page.update()
            ok, new_total = db.delete_receipt(del_rec_state["id"], current_job_id)
            e.control.disabled = False
            if ok:
                show_snack("Receipt permanently deleted.")
                if job: 
                    job['total_paid'] = new_total
                    db.log_history(job['id'], code_val, job.get('status_idx',0), job.get('status_idx',0), state["user"], "Finance and payments deleted")
                load_receipts()
            else: show_snack("Error deleting receipt.", is_error=True)
            close_any_dialog(confirm_del_rec_dialog)

        confirm_del_rec_dialog = ft.AlertDialog(title=ft.Text("Confirm Delete", color="red"), content=ft.Text("Are you sure you want to permanently delete this receipt?"), actions=[ft.TextButton("Cancel", on_click=lambda e: close_any_dialog(confirm_del_rec_dialog)), ft.ElevatedButton("Delete", color="white", bgcolor="red", on_click=execute_del_receipt)])

        def open_del_rec_confirm(r_id):
            if is_readonly: return
            del_rec_state["id"] = r_id; page.open(confirm_del_rec_dialog); page.update()

        def load_receipts():
            receipts_list.controls.clear()
            if not is_new:
                past_receipts = db.fetch_receipts(current_job_id)
                total_paid = 0.0
                for rec in past_receipts:
                    try: amt = float(rec.get('amount_paid', 0))
                    except: amt = 0.0
                    total_paid += amt
                    date_str = str(rec.get('payment_date', ''))[:10]
                    
                    receipt_row = ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.RECEIPT, size=20, color="green"),
                            ft.Text(f"{date_str} | No: {rec.get('receipt_no', '-')} | RM {amt:.2f}", size=14, expand=True),
                            ft.IconButton(ft.Icons.EDIT, icon_color="grey", icon_size=24, tooltip="Edit Receipt", on_click=lambda e, r=rec: open_edit_receipt(r)) if not is_readonly else ft.Container(),
                            ft.IconButton(ft.Icons.DELETE, icon_color="red", icon_size=24, tooltip="Delete Receipt", on_click=lambda e, r_id=rec['id']: open_del_rec_confirm(r_id)) if not is_readonly else ft.Container()
                        ]),
                        bgcolor=ft.Colors.GREEN_50, padding=10, border_radius=5
                    )
                    receipts_list.controls.append(receipt_row)
                receipts_list.controls.append(ft.Text(f"TOTAL PAID: RM {total_paid:.2f}", weight="bold", color="green", size=16))
            page.update()

        load_receipts()

        current_date_str = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")
        dlg_add_date = ft.TextField(label="Date (YYYY-MM-DD)", value=current_date_str, read_only=True, expand=True)
        btn_cal_add = ft.IconButton(icon=ft.Icons.CALENDAR_MONTH, icon_size=24, on_click=lambda e: open_date_picker(e, dlg_add_date))
        row_add_date = ft.Row([dlg_add_date, btn_cal_add])
        
        dlg_add_no = ft.TextField(label="Receipt Number")
        dlg_add_amt = ft.TextField(label="Amount (RM)", keyboard_type=ft.KeyboardType.NUMBER)

        def save_new_receipt(e):
            if not dlg_add_no.value or not dlg_add_amt.value or not dlg_add_date.value: show_snack("Please fill date, receipt no, and amount", is_error=True); return
            try: amt_val = float(dlg_add_amt.value)
            except ValueError: show_snack("Amount must be a valid number", is_error=True); return

            e.control.disabled = True; page.update()

            db_ready_date = f"{dlg_add_date.value}T12:00:00+08:00"
            rec_data = {"job_id": current_job_id, "receipt_no": dlg_add_no.value, "amount_paid": amt_val, "payment_date": db_ready_date}
            current_total = job.get('total_paid', 0) if job else 0
            ok, result = db.add_receipt(rec_data, current_total)
            
            e.control.disabled = False
            if ok: 
                show_snack("Receipt Added Successfully!")
                if job: 
                    job['total_paid'] = result
                    db.log_history(job['id'], code_val, job.get('status_idx',0), job.get('status_idx',0), state["user"], "Finance and payments updated")
                close_any_dialog(add_rec_dialog)
                load_receipts()
            else: show_snack(f"Failed to add receipt: {result}", is_error=True)

        add_rec_dialog = ft.AlertDialog(title=ft.Text("Add New Receipt"), content=ft.Column([row_add_date, dlg_add_no, dlg_add_amt], tight=True), actions=[ft.TextButton("Cancel", on_click=lambda e: close_any_dialog(add_rec_dialog)), ft.ElevatedButton("Save Receipt", color="white", bgcolor="green", on_click=save_new_receipt)])

        def open_add_receipt(e):
            if is_new: show_snack("You must SAVE the Job Card first before adding receipts!", is_error=True); return
            dlg_add_no.value = ""; dlg_add_amt.value = ""
            page.open(add_rec_dialog); page.update()

        btn_add_receipt = ft.ElevatedButton("Add Receipt", icon=ft.Icons.ADD, on_click=open_add_receipt)

        attachments_list = ft.Column(spacing=5)
        upload_progress = ft.ProgressBar(visible=False, color="blue")
        rename_state = {"doc_id": None}
        dlg_rename_input = ft.TextField(label="New File Name", autofocus=True)

        def save_renamed_doc(e):
            if not dlg_rename_input.value.strip(): show_snack("File name cannot be empty!", is_error=True); return
            e.control.disabled = True; page.update()
            ok, err = db.rename_attachment(rename_state["doc_id"], dlg_rename_input.value.strip())
            e.control.disabled = False
            if ok: show_snack("Document renamed successfully!"); close_any_dialog(rename_doc_dialog); load_attachments()
            else: show_snack(f"Failed to rename: {err}", is_error=True)

        rename_doc_dialog = ft.AlertDialog(title=ft.Text("Rename Document"), content=dlg_rename_input, actions=[ft.TextButton("Cancel", on_click=lambda e: close_any_dialog(rename_doc_dialog)), ft.ElevatedButton("Save Name", color="white", bgcolor="blue", on_click=save_renamed_doc)])

        def open_rename_dialog(doc_id, current_name):
            if is_readonly: return
            rename_state["doc_id"] = doc_id; dlg_rename_input.value = current_name
            page.open(rename_doc_dialog); page.update()

        del_doc_state = {"id": None, "link": None}
        
        def execute_del_doc(e):
            e.control.disabled = True; page.update()
            try:
                res = db.client.functions.invoke("bright-action", invoke_options={"body": {"file_link": del_doc_state["link"]}})
                ok, err = db.delete_attachment(del_doc_state["id"])
                
                if ok:
                    show_snack("Document completely deleted.")
                    if job: db.log_history(job['id'], code_val, job.get('status_idx',0), job.get('status_idx',0), state["user"], "Documents and attachments deleted")
                    load_attachments()
                else: show_snack(f"Failed to remove from database: {err}", is_error=True)
            except Exception as ex: show_snack(f"Delete Error: {str(ex)}", is_error=True)
                
            e.control.disabled = False
            close_any_dialog(confirm_del_doc_dialog)

        confirm_del_doc_dialog = ft.AlertDialog(title=ft.Text("Confirm Delete", color="red"), content=ft.Text("Are you sure you want to permanently delete this document?"), actions=[ft.TextButton("Cancel", on_click=lambda e: close_any_dialog(confirm_del_doc_dialog)), ft.ElevatedButton("Delete", color="white", bgcolor="red", on_click=execute_del_doc)])

        def open_del_doc_confirm(d_id, link):
            if is_readonly: return
            del_doc_state["id"] = d_id; del_doc_state["link"] = link; page.open(confirm_del_doc_dialog); page.update()

        def load_attachments():
            attachments_list.controls.clear()
            if not is_new:
                docs = db.fetch_attachments(current_job_id)
                if not docs: attachments_list.controls.append(ft.Text("No documents uploaded yet.", color="grey", italic=True, size=12))
                for doc in docs:
                    clickable_text = ft.Container(content=ft.Text(doc['file_name'], size=14, color="blue", style=ft.TextStyle(decoration=ft.TextDecoration.UNDERLINE)), expand=True, ink=True, on_click=lambda e, link=doc['file_link']: page.launch_url(link))
                    row_content = ft.Row([
                        ft.IconButton(ft.Icons.ATTACH_FILE, icon_color="blue", tooltip="Open Document", on_click=lambda e, link=doc['file_link']: page.launch_url(link)),
                        clickable_text,
                        ft.IconButton(ft.Icons.EDIT, icon_color="grey", icon_size=24, tooltip="Rename Document", on_click=lambda e, d_id=doc['id'], c_name=doc['file_name']: open_rename_dialog(d_id, c_name)) if not is_readonly else ft.Container(),
                        ft.IconButton(ft.Icons.DELETE, icon_color="red", icon_size=24, tooltip="Remove Document", on_click=lambda e, d_id=doc['id'], link=doc['file_link']: open_del_doc_confirm(d_id, link)) if not is_readonly else ft.Container()
                    ])
                    attachments_list.controls.append(ft.Container(content=row_content, padding=5, bgcolor=ft.Colors.BLUE_50, border_radius=5))
            page.update()

        load_attachments()

        def on_file_picked(e: ft.FilePickerResultEvent):
            if not e.files: return
            if is_new: show_snack("You must SAVE the Job Card first before uploading files!", is_error=True); return
            
            upload_progress.visible = True; page.update()
            
            success_count = 0
            for picked_file in e.files:
                try:
                    safe_name = f"{uuid.uuid4().hex[:8]}_{picked_file.name.replace(' ', '_')}"
                    
                    res = db.client.functions.invoke("clever-action", invoke_options={"body": {"filename": safe_name}})
                    res_data = json.loads(res)
                    upload_url = res_data.get("url")
                    
                    if upload_url:
                        file_data = get_file_bytes_compressed(picked_file.path)
                        cf_res = requests.put(upload_url, data=file_data)
                        
                        if cf_res.status_code == 200:
                            public_link = f"{CF_PUBLIC_URL}/{safe_name}"
                            att_data = {"job_id": current_job_id, "file_name": picked_file.name, "file_link": public_link, "uploaded_at": get_mys_iso(), "uploaded_by": state["user"]}
                            db_ok, db_err = db.add_attachment(att_data)
                            
                            if db_ok: 
                                success_count += 1
                                if job: db.log_history(job['id'], code_val, job.get('status_idx',0), job.get('status_idx',0), state["user"], f"Document '{picked_file.name}' uploaded")
                            else: show_snack(f"Failed to save {picked_file.name} to DB: {db_err}", is_error=True)
                        else: show_snack(f"Cloudflare rejected {picked_file.name}: {cf_res.text}", is_error=True)
                    else: show_snack(f"Edge function failed for {picked_file.name}", is_error=True)
                except Exception as ex:
                    show_snack(f"Upload crashed for {picked_file.name}: {str(ex)}", is_error=True)
            
            if success_count > 0:
                show_snack(f"{success_count} Document(s) Uploaded Securely!")
                load_attachments()
                
            upload_progress.visible = False; page.update()

        global_file_picker.on_result = on_file_picked
        
        def trigger_upload_photo(e):
            if is_new: show_snack("You must SAVE the Job Card first before adding files!", is_error=True); return
            global_file_picker.pick_files(allow_multiple=True, file_type=ft.FilePickerFileType.IMAGE)

        def trigger_upload_doc(e):
            if is_new: show_snack("You must SAVE the Job Card first before adding files!", is_error=True); return
            global_file_picker.pick_files(allow_multiple=True, allowed_extensions=["pdf", "doc", "docx", "xls", "xlsx", "txt", "csv"])

        btn_photo = ft.ElevatedButton("Upload Photo", icon=ft.Icons.CAMERA_ALT, on_click=trigger_upload_photo, bgcolor="blue", color="white")
        btn_doc = ft.ElevatedButton("Upload PDF / Doc", icon=ft.Icons.PICTURE_AS_PDF, on_click=trigger_upload_doc)
        
        upload_buttons = ft.Column([btn_photo, btn_doc]) if not is_readonly else ft.Container()

        attachments_section = ft.Container(content=ft.Column([ft.Text("Documents & Attachments", weight="bold", color="blue"), upload_progress, upload_buttons, attachments_list]), padding=10, border=ft.border.all(1, "blue"), border_radius=8)

        def save_click(e):
            if is_readonly: return
            e.control.disabled = True
            page.update()

            final_flag = int(dd_flag.value) if dd_flag.value else 0
            data = {"job_code": t_code.value, "memo_no": t_memo.value, "invoice_no": t_invoice.value, "do_no": t_do.value, "billed_by": t_billed.value, "customer": t_cust.value, "supervisor": t_pic.value, "trailer_type": t_type.value, "price_text": t_price.value, "summary": t_summary.value, "notes": t_notes.value, "price_breakdown": t_breakdown.value, "flagged": final_flag, "category": category_val, "payment_type": dd_payment_type.value, "chassis_no": t_chassis.value, "vehicle_no": t_vehicle.value}

            if is_new:
                data["status_idx"] = 0; data["closed"] = 0
                success, err = db.create_job(data, state["user"])
            else: success, err = db.update_job(job['id'], data, state["user"], t_code.value)

            e.control.disabled = False
            if success:
                if err: show_snack(f"Job Saved. Warning: {err}", is_error=True)
                else: show_snack("Job Saved Successfully")
                reload_current_view()
            else: 
                show_snack(f"Error: {err}", is_error=True)
                page.update()

        def move_status_click(e, target_idx):
            e.control.disabled = True; page.update()
            updates = {"status_idx": target_idx}
            if target_idx >= 8: updates["completed_at"] = get_mys_iso(); updates["closed"] = 1
            else: updates["closed"] = 0; updates["completed_at"] = None
            
            success, err = db.update_job(job['id'], updates, state["user"], job['job_code'], old_status=job['status_idx'], new_status=target_idx)
            e.control.disabled = False
            
            if success:
                if target_idx == 9: show_snack("Notification: Job has been moved to the Trash.", is_error=True)
                elif err: show_snack(f"Status Updated. Warning: {err}", is_error=True)
                
                if target_idx == 8: state["last_view_type"] = "archive" 
                elif target_idx == 9: state["last_view_type"] = "deleted"
                else: state["last_view_type"] = "status"; state["last_status_idx"] = target_idx; state["last_local_filter"] = ""; state["scroll_pos"] = 0.0
                reload_current_view()
            else: 
                show_snack(f"Error: {err}", is_error=True)
                page.update()

        def execute_hard_delete(e):
            e.control.disabled = True; page.update()
            success, err = db.hard_delete_job(job['id'], job['job_code'], state["user"])
            e.control.disabled = False
            if success:
                show_snack(f"Job {job['job_code']} permanently wiped from Database.")
                close_any_dialog(confirm_hard_del_dialog)
                handle_back_button() 
            else: show_snack(f"Failed to hard delete: {err}", is_error=True); page.update()

        confirm_hard_del_dialog = ft.AlertDialog(title=ft.Text("DANGER: Confirm Delete", color="red"), content=ft.Text("Are you sure you want to permanently delete this entire job? This action cannot be undone."), actions=[ft.TextButton("Cancel", on_click=lambda e: close_any_dialog(confirm_hard_del_dialog)), ft.ElevatedButton("DELETE EVERYTHING", color="white", bgcolor="red", on_click=execute_hard_delete)])

        def open_hard_delete_confirm():
            page.open(confirm_hard_del_dialog); page.update()

        page.appbar = ft.AppBar(leading=ft.IconButton(ft.Icons.ARROW_BACK, on_click=lambda e: reload_current_view()), title=ft.Text("Job Details", size=16), bgcolor="blue" if category_val == "new" else "orange", color="white", actions=[ft.IconButton(ft.Icons.CHECK, on_click=save_click) if not is_readonly else ft.Container()])

        status_buttons = []
        if not is_new and not is_readonly:
            btns_to_show = STATUSES_NEW if category_val == "new" else STATUSES_USED
            current_s = job.get('status_idx', 0)
            current_label = STATUS_DICT.get(current_s, 'Unknown')
            status_buttons.append(ft.Container(content=ft.Row([ft.Icon(ft.Icons.INFO, color="blue"), ft.Text(f"Current Status: {current_label}", weight="bold", size=16, color="blue")]), bgcolor=ft.Colors.BLUE_50, padding=10, border_radius=8))
            status_buttons.append(ft.Divider(height=10, color="transparent")); status_buttons.append(ft.Text("Move Status To:", weight="bold"))
            
            row_btns = []
            for idx, label in btns_to_show:
                if idx < 8 and idx != current_s: row_btns.append(ft.OutlinedButton(label.split(" - ")[0], on_click=lambda e, i=idx: move_status_click(e, i)))
            status_buttons.append(ft.Column(row_btns))
            
            if current_s < 9:
                status_buttons.append(ft.Divider())
                action_row_controls = []
                if current_s == 7: action_row_controls.append(ft.ElevatedButton("CLOSE (ARCHIVE)", color="white", bgcolor="green", on_click=lambda e: move_status_click(e, 8)))
                action_row_controls.append(ft.ElevatedButton("SEND TO TRASH", color="white", bgcolor="red", on_click=lambda e: move_status_click(e, 9)))
                status_buttons.append(ft.Row(action_row_controls, wrap=True))
            
            if current_s == 9:
                status_buttons.append(ft.Divider())
                status_buttons.append(ft.ElevatedButton("PERMANENTLY DELETE FROM DATABASE", color="white", bgcolor="red", icon=ft.Icons.DELETE_FOREVER, on_click=lambda e: open_hard_delete_confirm()))

        finance_section = ft.Container(content=ft.Column([ft.Text("Finance & Payments", weight="bold", color="green"), dd_payment_type, btn_add_receipt if not is_readonly else ft.Container(), receipts_list]), padding=10, border=ft.border.all(1, "green"), border_radius=8)
        btn_view_history_details = ft.ElevatedButton("View Job History", icon=ft.Icons.HISTORY, on_click=lambda e: view_specific_history(e, job), bgcolor=ft.Colors.BLUE_50, color="blue") if not is_new else ft.Container(height=0)

        page.add(ft.Container(
            content=ft.Column([
                responsive_row([t_code, t_memo]), t_billed, responsive_row([t_invoice, t_do]), responsive_row([t_chassis, t_vehicle]),
                t_cust, t_pic, t_summary, t_type, t_price, dd_flag, 
                btn_view_history_details,
                finance_section, attachments_section, 
                ft.Text("Notes & Costing", weight="bold", color="grey"), t_notes, t_breakdown, ft.Divider(), ft.Column(status_buttons)
            ], scroll=ft.ScrollMode.ADAPTIVE, expand=True, spacing=15), 
            padding=15, expand=True, alignment=ft.alignment.top_center
        ))

    def load_history_view(job_code_filter=None):
        page.clean(); state["in_details"] = False; state["history_filter_job"] = job_code_filter
        title_str = f"History: {job_code_filter}" if job_code_filter else "Global System History"
        
        leading_icon = ft.IconButton(ft.Icons.ARROW_BACK, on_click=lambda e: handle_back_button()) if job_code_filter else ft.IconButton(ft.Icons.MENU, on_click=safe_open_drawer)

        page.appbar = ft.AppBar(leading=leading_icon, title=ft.Text(title_str, size=14), bgcolor="blue", color="white")
        logs = db.fetch_history(job_code_filter)
        lv = ft.ListView(expand=True, padding=10, spacing=5)
        
        if not logs: lv.controls.append(ft.Text("No history found.", text_align="center"))

        for log in logs:
            try:
                job_c = log.get('job_code', 'Unknown'); user = log.get('changed_by', 'System'); old_s = STATUS_DICT.get(log.get('old_status'), '?')
                new_s_id = log.get('new_status')
                if new_s_id == 8: new_s = "Closed"
                elif new_s_id == 9: new_s = "Deleted"
                else: new_s = STATUS_DICT.get(new_s_id, 'Unknown')

                raw_time = str(log.get('changed_at', '')); time = raw_time.replace('T', ' ')[:16] if raw_time else ""
                details = log.get('details', '')
                if details: msg = details
                elif log.get('old_status') == -1: msg = "Job Created"
                elif log.get('old_status') != log.get('new_status'): msg = f"{old_s} -> {new_s}"
                else: msg = "Edited"
                
                tile = ft.Container(content=ft.Column([ft.Row([ft.Text(job_c, weight="bold"), ft.Text(time, size=10, color="grey")], alignment="spaceBetween"), ft.Text(f"{user}: {msg}", size=12)]), padding=10, bgcolor=ft.Colors.GREY_100, border_radius=5)
                lv.controls.append(tile)
            except Exception as e:
                print(f"Skipped bad history view log: {e}")
        
        page.add(ft.Container(content=lv, alignment=ft.alignment.top_center, expand=True))

    show_login()

ft.app(target=main, assets_dir="assets")
