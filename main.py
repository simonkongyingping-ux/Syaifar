import flet as ft
from supabase import create_client, Client
from datetime import datetime, timedelta, timezone
import traceback 

# Paste your actual keys here. The .strip() removes invisible spaces!
SUPABASE_URL = "https://qqxaujdifamluzlhixvv.supabase.co".strip()
SUPABASE_KEY = "sb_publishable_lVx4h2kOKOB6_zenbJRH_g_GNCt5TgX".strip()

# --- VERSION ---
APP_VERSION = "v2.2"

# --- CONFIGURATION ---
STATUSES_NEW = [
    (0, "0% - Job Created"),
    (1, "10% - Chassis Fab Started"),
    (2, "30% - Body Fab Started"),
    (3, "50% - Painting Started"),
    (4, "80% - Fittings Started"),
    (5, "90% - PUS / JPJ"),
    (6, "100% - Ready For Delivery"),
    (7, "Delivered"),
    (8, "Closed / Archived"),
    (9, "Deleted / Trash"),
]

STATUSES_USED = [
    (0, "0% - Job Created"),
    (3, "50% - Work In Progress"),
    (6, "100% - Ready For Delivery"),
    (7, "Delivered"),
    (8, "Closed / Archived"),
    (9, "Deleted / Trash"),
]

STATUS_DICT = {idx: label for idx, label in STATUSES_NEW}
STATUS_DICT[3] = "50% - Painting / WIP" 

# --- HELPER: MALAYSIAN TIME ---
def get_mys_iso():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()

# --- DATABASE MANAGER ---
class DbManager:
    def __init__(self):
        if SUPABASE_URL and SUPABASE_KEY:
            self.client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        else:
            self.client = None

    def fetch_users(self):
        if not self.client: return []
        try:
            res = self.client.table("app_users").select("username").execute()
            return [r['username'] for r in res.data]
        except:
            return []

    def login(self, username, password):
        if not self.client: return False, "DB Connection Failed"
        try:
            res = self.client.table("app_users").select("*").eq("username", username).eq("password", password).execute()
            if res.data:
                raw_role = res.data[0].get("role", "guest")
                clean_role = str(raw_role).lower().strip()
                return True, clean_role
            return False, "Invalid Username or Password"
        except Exception as e:
            return False, str(e)

    def fetch_jobs(self, category="new", status_filter=None, search_term=None):
        if not self.client: return []
        try:
            query = self.client.table("memo_system").select("*")
            query = query.eq("category", category)

            if search_term:
                or_str = f"job_code.ilike.%{search_term}%,memo_no.ilike.%{search_term}%,do_no.ilike.%{search_term}%,invoice_no.ilike.%{search_term}%,customer.ilike.%{search_term}%,supervisor.ilike.%{search_term}%"
                query = query.or_(or_str)
            else:
                if status_filter == 8: 
                    query = query.eq("status_idx", 8)
                elif status_filter == 9:
                    query = query.eq("status_idx", 9)
                else:
                    query = query.neq("status_idx", 8).neq("status_idx", 9)
                    if status_filter is not None:
                        query = query.eq("status_idx", status_filter)
            
            res = query.order("updated_at", desc=True).execute()
            return res.data
        except Exception as e:
            return []

    def fetch_history(self, job_code=None):
        if not self.client: return []
        try:
            query = self.client.table("job_history").select("*").order("changed_at", desc=True)
            if job_code:
                query = query.eq("job_code", job_code)
            res = query.limit(50).execute()
            return res.data
        except:
            return []

    def create_job(self, data, user):
        if not self.client: return False, "No DB Connection"
        try:
            data["created_at"] = get_mys_iso()
            data["updated_at"] = get_mys_iso()
            
            res = self.client.table("memo_system").insert(data).execute()
            if res.data:
                new_id = res.data[0]['id']
                hist_ok, hist_err = self.log_history(new_id, data['job_code'], None, 0, user, "Job Created")
                if not hist_ok:
                    return True, f"Job Created, but Log Failed: {hist_err}"
                return True, None
            return False, "Insert failed (No data returned)"
        except Exception as e:
            return False, str(e)

    def update_job(self, job_id, new_data, user, job_code, old_status=None, new_status=None):
        if not self.client: return False, "No DB Connection"
        try:
            current_res = self.client.table("memo_system").select("*").eq("id", job_id).execute()
            if not current_res.data: return False, "Job not found"
            old_data = current_res.data[0]

            new_data["updated_at"] = get_mys_iso()
            self.client.table("memo_system").update(new_data).eq("id", job_id).execute()
            
            log_msg = None
            log_old_s = old_data['status_idx']
            log_new_s = old_data['status_idx']

            if old_status is not None and new_status is not None and old_status != new_status:
                old_lbl = STATUS_DICT.get(old_status, "?")
                new_lbl = STATUS_DICT.get(new_status, "?")
                old_short = old_lbl.split(" - ")[0]
                new_short = new_lbl.split(" - ")[0]
                if old_status >= 8: old_short = STATUS_DICT[old_status].split(" / ")[0]
                if new_status >= 8: new_short = STATUS_DICT[new_status].split(" / ")[0]
                log_msg = f"{old_short} -> {new_short}"
                log_old_s = old_status
                log_new_s = new_status
            else:
                core_fields = ["customer", "price_text", "memo_no", "invoice_no", "do_no", "billed_by", "job_code", "flagged"]
                core_changed = any(str(old_data.get(f,'')) != str(new_data.get(f,'')) for f in core_fields if f in new_data)
                
                if core_changed: log_msg = "Details Updated"
                elif "price_breakdown" in new_data and str(old_data.get("price_breakdown",'')) != str(new_data["price_breakdown"]): log_msg = "Price Breakdown Updated"
                elif "notes" in new_data and str(old_data.get("notes",'')) != str(new_data["notes"]): log_msg = "Notes Updated"

            if log_msg:
                final_job_code = new_data.get("job_code", job_code)
                hist_ok, hist_err = self.log_history(job_id, final_job_code, log_old_s, log_new_s, user, log_msg)
                if not hist_ok:
                    return True, f"Saved, but History Failed: {hist_err}"

            return True, None
        except Exception as e:
            return False, str(e)

    def log_history(self, job_id, job_code, old_s, new_s, user, details_text=""):
        if not self.client: return False, "No Client"
        try:
            safe_old = old_s if old_s is not None else -1
            safe_new = new_s if new_s is not None else 0

            log = {
                "job_id": job_id,
                "job_code": job_code,
                "old_status": safe_old,
                "new_status": safe_new,
                "changed_by": user,
                "changed_at": get_mys_iso(),
                "details": details_text
            }
            self.client.table("job_history").insert(log).execute()
            return True, None
        except Exception as e:
            return False, str(e)

db = DbManager()

# --- MAIN APP ---
def main(page: ft.Page):
    # PLATFORM DETECTION
    is_mobile = page.platform in [ft.PagePlatform.ANDROID, ft.PagePlatform.IOS]

    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 5 if is_mobile else 0
    page.title = f"Syaifar's Kanban {APP_VERSION}"
    
    if not is_mobile:
        page.window.min_width = 1000
        page.window.min_height = 700
        page.window.center()

    state = {
        "user": "",
        "role": "",
        "category": "new",  
        "last_view_type": "overview", 
        "last_status_idx": None,
        "last_search_term": "",
        "last_local_filter": "",
        "history_filter_job": None, 
        "feed_limit": 10, 
        "scroll_pos": 0.0,
        "in_details": False 
    }

    def handle_back_button(e=None):
        try:
            if page.drawer and page.drawer.open:
                page.drawer.open = False
                page.update()
                return True
            if state["in_details"]:
                state["in_details"] = False
                reload_current_view()
                page.update()
                return True
            if state["user"] != "" and state["last_view_type"] != "overview":
                state["last_view_type"] = "overview"
                load_job_list_view("Overview Dashboard")
                page.update()
                return True
            return False
        except Exception as ex:
            return False

    page.on_back_button = handle_back_button

    def on_keyboard(e: ft.KeyboardEvent):
        if e.key == "Escape":
            handle_back_button()
    page.on_keyboard_event = on_keyboard
    page.update()

    def show_snack(msg, is_error=False):
        snack = ft.SnackBar(content=ft.Text(msg), bgcolor=ft.Colors.RED if is_error else ft.Colors.GREEN)
        page.overlay.append(snack)
        snack.open = True
        page.update()

    def safe_open_drawer(e):
        page.drawer.open = True
        page.update()

    def get_drawer():
        current_status_list = STATUSES_NEW if state["category"] == "new" else STATUSES_USED
        
        nav_controls = [
            ft.Container(height=20),
            ft.Text(f"  MODE: {state['category'].upper()}", weight="bold", size=16, color="red"),
            ft.Container(height=10),
            
            ft.NavigationDrawerDestination(icon=ft.Icons.CONSTRUCTION, label="New Construction"),
            ft.NavigationDrawerDestination(icon=ft.Icons.CAR_REPAIR, label="Used / Refurb"),
            ft.Divider(thickness=1),
            
            ft.NavigationDrawerDestination(icon=ft.Icons.DASHBOARD, label="Overview (Dashboard)"),
            ft.NavigationDrawerDestination(icon=ft.Icons.SEARCH, label="Global Search"),
            ft.NavigationDrawerDestination(icon=ft.Icons.NOTIFICATIONS_ACTIVE, label="Live Activity Feed"),
            ft.Divider(thickness=1),
        ]

        for idx, label in current_status_list:
            if idx < 8: 
                nav_controls.append(ft.NavigationDrawerDestination(icon=ft.Icons.CIRCLE_OUTLINED, label=label))

        nav_controls.extend([
            ft.Divider(thickness=1),
            ft.NavigationDrawerDestination(icon=ft.Icons.ARCHIVE, label="Closed / Archived"),
            ft.NavigationDrawerDestination(icon=ft.Icons.DELETE_OUTLINE, label="Trash / Deleted"),
            ft.NavigationDrawerDestination(icon=ft.Icons.HISTORY, label="Global History"),
            ft.NavigationDrawerDestination(icon=ft.Icons.LOGOUT, label="Logout"),
            ft.Container(content=ft.Text(APP_VERSION, color="grey", size=12), padding=ft.padding.only(left=20, top=10, bottom=10))
        ])

        return ft.NavigationDrawer(controls=nav_controls, on_change=on_nav_change)

    def on_nav_change(e):
        try:
            idx = e.control.selected_index
            state["last_local_filter"] = ""
            state["scroll_pos"] = 0.0
            state["in_details"] = False
            state["history_filter_job"] = None 
            
            current_status_list = STATUSES_NEW if state["category"] == "new" else STATUSES_USED
            status_count = len([s for s in current_status_list if s[0] < 8])

            if idx == 0: 
                state["category"] = "new"
                state["last_view_type"] = "overview"
                page.drawer = get_drawer()
                load_job_list_view("New Construction Dashboard")
            elif idx == 1: 
                state["category"] = "used"
                state["last_view_type"] = "overview"
                page.drawer = get_drawer()
                load_job_list_view("Used / Refurb Dashboard")
            elif idx == 2: 
                state["last_view_type"] = "overview"
                load_job_list_view("Overview Dashboard")
            elif idx == 3: 
                show_search_view()
            elif idx == 4: 
                state["last_view_type"] = "live_feed"
                load_live_feed_view()
            elif 5 <= idx < (5 + status_count): 
                list_idx = idx - 5
                status_idx = current_status_list[list_idx][0]
                state["last_view_type"] = "status"
                state["last_status_idx"] = status_idx
                load_job_list_view(STATUS_DICT.get(status_idx, "Status View"))
            elif idx == (5 + status_count): 
                state["last_view_type"] = "archive"
                state["last_status_idx"] = 8
                load_job_list_view("Archived Jobs")
            elif idx == (5 + status_count + 1): 
                state["last_view_type"] = "deleted"
                state["last_status_idx"] = 9
                load_job_list_view("Trash / Deleted Jobs")
            elif idx == (5 + status_count + 2): 
                state["last_view_type"] = "history"
                load_history_view()
            elif idx == (5 + status_count + 3): 
                state["user"] = ""
                show_login()
                return

            if page.drawer: page.drawer.open = False
            page.update()
        except Exception as ex:
            print(f"Nav Error: {ex}")

    def reload_current_view():
        view_type = state["last_view_type"]
        state["in_details"] = False 

        if view_type == "overview": load_job_list_view(f"{state['category'].capitalize()} Dashboard")
        elif view_type == "status": load_job_list_view(STATUS_DICT.get(state["last_status_idx"], "Status View"))
        elif view_type == "archive": load_job_list_view("Archived Jobs")
        elif view_type == "deleted": load_job_list_view("Trash / Deleted Jobs")
        elif view_type == "search": load_job_list_view(f"Results: {state['last_search_term']}", is_global_search=True)
        elif view_type == "live_feed": load_live_feed_view()
        elif view_type == "history": load_history_view(state["history_filter_job"])
        else: load_job_list_view("Overview Dashboard")

    # --- SCREENS ---
    def show_login():
        page.clean()
        page.appbar = None
        page.drawer = None
        state["in_details"] = False
        
        users_list = []
        connection_status = "Connecting..."
        try:
            users_list = db.fetch_users()
            connection_status = "Connected"
        except:
            connection_status = "Offline / Connection Error"

        dropdown_options = [ft.dropdown.Option(u) for u in users_list] if users_list else [ft.dropdown.Option("admin")]
        user_dropdown = ft.Dropdown(label="Select User", width=280, options=dropdown_options, autofocus=True)
        pass_in = ft.TextField(label="Password", password=True, width=280)
        status_lbl = ft.Text(connection_status, color="grey")

        def attempt_login(e):
            if not user_dropdown.value:
                status_lbl.value = "Select a user."
                page.update()
                return

            status_lbl.value = "Checking..."
            page.update()
            
            success, msg = db.login(user_dropdown.value, pass_in.value)
            if success:
                state["user"] = user_dropdown.value
                state["role"] = msg 
                page.drawer = get_drawer()
                reload_current_view() 
            else:
                status_lbl.value = msg
                status_lbl.color = "red"
                page.update()

        page.add(
            ft.Stack([
                ft.Container(
                    content=ft.Column([
                        ft.Icon(ft.Icons.DIRECTIONS_CAR, size=60, color="blue"),
                        ft.Text("Syaifar's Kanban", size=24, weight="bold"),
                        ft.Text("Job Management System", size=16),
                        ft.Divider(height=20, color="transparent"),
                        user_dropdown, pass_in,
                        ft.ElevatedButton("Login", on_click=attempt_login, bgcolor="blue", color="white"),
                        status_lbl
                    ], horizontal_alignment="center", alignment=ft.MainAxisAlignment.CENTER, scroll=ft.ScrollMode.ADAPTIVE),
                    alignment=ft.alignment.center, expand=True, bgcolor=ft.Colors.BLUE_50, padding=20
                ),
                ft.Container(content=ft.Text(APP_VERSION, color="grey", size=12, weight="bold"), bottom=10, left=10)
            ], expand=True)
        )

    def show_search_view():
        page.clean()
        state["last_local_filter"] = "" 
        state["in_details"] = False
        
        search_box = ft.TextField(label="Search (Code, Memo, DO, Inv, Cust)", expand=True, autofocus=True)
        def run_search(e):
            if not search_box.value: return
            state["last_view_type"] = "search"
            state["last_search_term"] = search_box.value
            load_job_list_view(f"Results: {search_box.value}", is_global_search=True)

        page.appbar = ft.AppBar(leading=ft.IconButton(ft.Icons.MENU, on_click=safe_open_drawer), title=ft.Text("Global Search"), bgcolor="blue", color="white")
        page.add(ft.Container(content=ft.Column([ft.Row([search_box, ft.IconButton(ft.Icons.SEARCH, on_click=run_search)]), ft.Divider()]), padding=10))

    def load_live_feed_view():
        page.clean()
        state["in_details"] = False
        limit = state.get("feed_limit", 10)

        def change_limit(e):
            state["feed_limit"] = int(e.control.value)
            load_live_feed_view()

        dd_limit = ft.Dropdown(
            label="Filter Events",
            options=[
                ft.dropdown.Option("10", "Last 10 Events"),
                ft.dropdown.Option("20", "Last 20 Events"),
                ft.dropdown.Option("30", "Last 30 Events"),
            ],
            value=str(limit), width=200, on_change=change_limit
        )

        page.appbar = ft.AppBar(leading=ft.IconButton(ft.Icons.MENU, on_click=safe_open_drawer), title=ft.Text("Live Activity Feed"), bgcolor="blue", color="white")
        
        history_logs = db.fetch_history()[:limit] 
        feed_list = ft.ListView(expand=True, spacing=10, padding=15)
        
        if not history_logs:
            feed_list.controls.append(ft.Text("No recent activity.", color="grey", text_align="center"))
        else:
            for log in history_logs:
                job_c = log.get('job_code', 'Unknown')
                user = log.get('changed_by', 'System')
                old_s = STATUS_DICT.get(log.get('old_status'), '?')
                
                new_s_id = log.get('new_status')
                if new_s_id == 8: new_s = "Closed"
                elif new_s_id == 9: new_s = "Deleted"
                else: new_s = STATUS_DICT.get(new_s_id, 'Unknown')

                raw_time = str(log.get('changed_at', ''))
                time_str = raw_time.replace('T', ' ')[:16] if raw_time else ""
                
                details = log.get('details', '')
                if details: msg = details
                elif log.get('old_status') == -1: msg = "Job Created"
                elif log.get('old_status') != log.get('new_status'): msg = f"{old_s} -> {new_s}"
                else: msg = "Edited"
                
                feed_list.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.HISTORY, size=18, color="blue"),
                            ft.Text(f"{time_str} | ", size=14, color="grey", weight="bold"),
                            ft.Text(f"{user} updated ", size=14, color="grey"),
                            ft.Text(f"{job_c}: ", size=14, weight="bold", color="black"),
                            ft.Text(f"{msg}", size=14, color="black87")
                        ], vertical_alignment="center", wrap=True),
                        bgcolor=ft.Colors.GREY_100, padding=15, border_radius=8
                    )
                )
        
        page.add(
            ft.Container(
                content=ft.Column([
                    ft.Container(content=dd_limit, padding=10),
                    ft.Divider(height=1, color="grey"),
                    feed_list
                ], expand=True),
                expand=True
            )
        )

    def load_job_list_view(title, is_global_search=False):
        page.clean()
        state["in_details"] = False
        current_status_list = STATUSES_NEW if state["category"] == "new" else STATUSES_USED

        # APPBAR
        appbar_actions = []
        if state["role"] == "admin":
             appbar_actions.append(ft.IconButton(ft.Icons.ADD, on_click=lambda e: show_job_details(None), tooltip="Create New Job"))
        appbar_actions.append(ft.IconButton(ft.Icons.REFRESH, on_click=lambda e: reload_current_view(), tooltip="Refresh"))

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.Icons.MENU, on_click=safe_open_drawer), 
            title=ft.Text(title, size=16), 
            bgcolor="blue" if state["category"] == "new" else "orange", 
            color="white", actions=appbar_actions
        )

        # FETCH DATA
        if is_global_search: jobs = db.fetch_jobs(category=state["category"], search_term=state["last_search_term"])
        elif state["last_view_type"] == "archive": jobs = db.fetch_jobs(category=state["category"], status_filter=8)
        elif state["last_view_type"] == "deleted": jobs = db.fetch_jobs(category=state["category"], status_filter=9)
        elif state["last_view_type"] == "status": jobs = db.fetch_jobs(category=state["category"], status_filter=state["last_status_idx"])
        else: jobs = db.fetch_jobs(category=state["category"], status_filter=None)

        # DASHBOARD VIEW
        if state["last_view_type"] == "overview":
            stats = {idx: {'total': 0, 'flags': 0} for idx, _ in current_status_list if idx < 8}
            for j in jobs:
                s_idx = j.get('status_idx', 0)
                if s_idx in stats:
                    stats[s_idx]['total'] += 1
                    raw_f = j.get('flagged', 0)
                    if str(raw_f) not in ['0', 'False', 'None', '']: stats[s_idx]['flags'] += 1

            def open_status_view(idx):
                state["last_view_type"] = "status"
                state["last_status_idx"] = idx
                state["last_local_filter"] = "" 
                state["scroll_pos"] = 0.0
                reload_current_view()

            dash_cols = 1 if is_mobile else 2
            dash_ratio = 2.0 if is_mobile else 3.0
            
            dashboard_col = ft.GridView(expand=True, runs_count=dash_cols, child_aspect_ratio=dash_ratio, spacing=10, run_spacing=10, padding=10)
            
            for idx, label in current_status_list:
                if idx >= 8: continue 
                total = stats.get(idx, {}).get('total', 0)
                flagged = stats.get(idx, {}).get('flags', 0)
                
                flag_color = ft.Colors.RED if flagged > 0 else ft.Colors.GREY
                flag_bg = ft.Colors.RED_50 if flagged > 0 else ft.Colors.TRANSPARENT
                
                card_content = ft.Container(
                    content=ft.Row([
                        ft.Column([ft.Text(label, weight="bold", size=16), ft.Text(f"{total} Active Jobs", color="grey", size=13)], alignment="center"), 
                        ft.Container(content=ft.Column([ft.Icon(ft.Icons.FLAG, color=flag_color, size=20), ft.Text(f"{flagged}", color=flag_color, weight="bold")], horizontal_alignment="center", spacing=2), padding=10, border_radius=8, bgcolor=flag_bg)
                    ], alignment="spaceBetween"),
                    padding=20, on_click=lambda e, i=idx: open_status_view(i)
                )
                dashboard_col.controls.append(ft.Card(content=card_content, color="white", elevation=2))

            main_dash_view = ft.Column([
                ft.Container(content=ft.Text(f"Active {state['category'].capitalize()} Jobs: {len(jobs)}", size=20, weight="bold", color="blue"), padding=10),
                ft.Container(content=dashboard_col, expand=True)
            ], expand=True)

            page.add(ft.Container(content=main_dash_view, expand=True))
            return 

        # LIST VIEW LOGIC
        def on_scroll_list(e: ft.OnScrollEvent): state["scroll_pos"] = e.pixels

        # THE SMART SWITCH: ListView for Mobile, GridView for Desktop
        if is_mobile:
            list_container = ft.ListView(expand=True, spacing=10, padding=10, on_scroll=on_scroll_list)
        else:
            list_container = ft.GridView(expand=True, runs_count=2, child_aspect_ratio=1.4, spacing=10, run_spacing=10, padding=10, on_scroll=on_scroll_list)

        def view_specific_history(e, j_code):
            state["last_view_type"] = "history"
            load_history_view(j_code)

        def draw_cards(job_list):
            list_container.controls.clear()
            if not job_list: 
                list_container.controls.append(ft.Text("No jobs found.", text_align="center", color="grey"))
                return

            for job in job_list:
                flag_raw = job.get('flagged', 0)
                flag_val = 1 if flag_raw is True else (0 if flag_raw is False else int(flag_raw) if str(flag_raw).isdigit() else 0)

                flag_text_ui = None
                if flag_val == 1:
                    card_bg = ft.Colors.RED_500
                    text_color = "white"
                    sub_text_color = "white70"
                    icon_color = "white"
                    flag_text_ui = ft.Text("🚨 NO DEPOSIT & SUSPENDED", color="white", weight="bold", size=13)
                elif flag_val == 2:
                    card_bg = ft.Colors.ORANGE_400
                    text_color = "black"
                    sub_text_color = "black54"
                    icon_color = "black87"
                    flag_text_ui = ft.Text("⚠️ NO DEPOSIT & WIP", color="black", weight="bold", size=13)
                elif flag_val == 3:
                    card_bg = ft.Colors.YELLOW_300
                    text_color = "black"
                    sub_text_color = "black54"
                    icon_color = "black87"
                    flag_text_ui = ft.Text("⏳ FOR STANDBY PURPOSE", color="black", weight="bold", size=13)
                else:
                    card_bg = "white"
                    text_color = "black"
                    sub_text_color = "grey"
                    icon_color = "blue"

                memo_val = job.get('memo_no')
                inv_val = job.get('invoice_no', '')
                bill_val = job.get('billed_by', '')
                do_val = job.get('do_no', '')
                
                raw_created = str(job.get('created_at', ''))
                raw_updated = str(job.get('updated_at', ''))
                nice_created = raw_created.replace('T', ' ')[:16] if raw_created else ""
                nice_updated = raw_updated.replace('T', ' ')[:16] if raw_updated else ""
                
                date_col_controls = [ft.Text(f"Cr: {nice_created}", size=11, color=sub_text_color)]
                if nice_updated and nice_updated != nice_created:
                    date_col_controls.append(ft.Text(f"Upd: {nice_updated}", size=11, color=sub_text_color))

                card_content = ft.Container(
                    # NOTE: Removed scroll=ft.ScrollMode.ADAPTIVE here so it doesn't hijack your thumb!
                    content=ft.Column([
                        ft.Row([
                            ft.Text(job['job_code'], weight="bold", size=16, color=text_color),
                            ft.Column(date_col_controls, alignment="end", spacing=0) 
                        ], alignment="spaceBetween"),
                        
                        flag_text_ui if flag_text_ui else ft.Container(height=0),

                        ft.Text(f"Memo: {memo_val if memo_val else '-'}", weight="bold", size=14, color=ft.Colors.BLUE_900 if flag_val>0 else ft.Colors.BLUE),
                        ft.Text(f"Inv: {inv_val if inv_val else '-'}  |  DO: {do_val if do_val else '-'}", size=12, color=text_color),
                        
                        # CHANGED TO: "Billed by"
                        ft.Text(f"Billed by: {bill_val if bill_val else '-'}", size=12, color=sub_text_color),
                        
                        # REMOVED no_wrap=True and overflow limit so names can wrap!
                        ft.Text(f"Cust: {job.get('customer', '-')}", size=14, color=text_color),
                        ft.Text(f"Type: {job.get('trailer_type', '-')}", size=12, color=sub_text_color),
                        ft.Text(f"Price: {job.get('price_text', '-')}", size=12, color=sub_text_color),
                        ft.Text(f"PIC: {job.get('supervisor', '-')}", size=12, color=sub_text_color),

                        ft.Divider(height=5, color="transparent"),
                        ft.Text(f"{job.get('summary', '')}", size=12, italic=True, color=text_color, max_lines=3, overflow="ellipsis"),
                        
                        ft.Row([
                            ft.IconButton(ft.Icons.HISTORY, icon_size=18, icon_color=icon_color, tooltip="View Job History", on_click=lambda e, jc=job['job_code']: view_specific_history(e, jc)),
                            ft.Row([
                                ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color=icon_color),
                                ft.Text(f"{STATUS_DICT.get(job['status_idx'], 'Unknown')}", size=12, color=icon_color)
                            ])
                        ], alignment="spaceBetween")
                    ], spacing=5), 
                    padding=15,
                    on_click=lambda e, j=job: show_job_details(j)
                )
                list_container.controls.append(ft.Card(content=card_content, color=card_bg))
            page.update()

        # SEARCH BAR
        top_bar = None
        if is_global_search:
            search_input = ft.TextField(value=state["last_search_term"], label="Search Database", hint_text="Code / Memo / DO / Inv / Cust...", expand=True, height=50, text_size=14, content_padding=10)

            def run_search_trigger(e):
                new_term = search_input.value
                if not new_term: return
                state["last_search_term"] = new_term
                load_job_list_view(f"Results: {new_term}", is_global_search=True)

            search_input.on_submit = run_search_trigger
            search_btn = ft.IconButton(icon=ft.Icons.SEARCH, icon_color="blue", tooltip="Click to Search", on_click=run_search_trigger)
            top_bar = ft.Row([search_input, search_btn], spacing=5)
            draw_cards(jobs)

        else:
            def run_local_filter(e):
                filter_text = e.control.value.lower()
                state["last_local_filter"] = filter_text
                visible_jobs = []
                if not filter_text: visible_jobs = jobs
                else:
                    for j in jobs:
                        full_text = f"{j['job_code']} {j.get('memo_no','')} {j.get('do_no','')} {j.get('invoice_no','')} {j.get('customer','')} {j.get('supervisor','')} {j.get('summary','')}".lower()
                        if filter_text in full_text: visible_jobs.append(j)
                draw_cards(visible_jobs)

            top_bar = ft.TextField(value=state.get("last_local_filter", ""), hint_text=f"Filter these {len(jobs)} jobs...", prefix_icon=ft.Icons.FILTER_LIST, height=50, text_size=14, content_padding=10, on_change=run_local_filter)
            run_local_filter(ft.ControlEvent(target="", name="change", data=state.get("last_local_filter", ""), control=top_bar, page=page))

        page.add(ft.Container(content=ft.Column([ft.Container(content=top_bar, padding=10), list_container], spacing=0, expand=True), expand=True, alignment=ft.alignment.top_center))
        if state["scroll_pos"] > 0: list_container.scroll_to(offset=state["scroll_pos"], duration=0)

    def show_job_details(job):
        page.clean()
        state["in_details"] = True 

        is_new = job is None
        is_readonly = state["role"] != "admin"

        flag_raw = job.get('flagged', 0) if job else 0
        flag_val = 1 if flag_raw is True else (0 if flag_raw is False else int(flag_raw) if str(flag_raw).isdigit() else 0)

        category_val = job.get('category', state["category"]) if job else state["category"]
        id_label = "Job Code" if category_val == "new" else "Unit ID / Reg No"
        code_val = job['job_code'] if job else f"{'JOB' if category_val=='new' else 'USED'}-{int(datetime.utcnow().timestamp())}"
        
        def responsive_row(controls):
            if is_mobile:
                return ft.Column(controls, spacing=10)
            return ft.Row(controls, spacing=10)

        t_code = ft.TextField(label=id_label, value=code_val, disabled=False, expand=True)
        t_memo = ft.TextField(label="Memo NO", value=job.get('memo_no','') if job else "", expand=True)
        t_invoice = ft.TextField(label="Invoice No", value=job.get('invoice_no','') if job else "", expand=True)
        t_do = ft.TextField(label="DO Number", value=job.get('do_no','') if job else "", expand=True)
        t_billed = ft.TextField(label="Billed By", value=job.get('billed_by','') if job else "")

        t_cust = ft.TextField(label="Customer", value=job.get('customer','') if job else "")
        t_pic = ft.TextField(label="PIC / Supervisor", value=job.get('supervisor','') if job else "")
        t_type = ft.TextField(label="Trailer/Unit Type", value=job.get('trailer_type','') if job else "")
        t_price = ft.TextField(label="Price (Total)", value=job.get('price_text','') if job else "")
        t_summary = ft.TextField(label="Summary", value=job.get('summary','') if job else "")
        t_notes = ft.TextField(label="Production Notes", value=job.get('notes','') if job else "", multiline=True, min_lines=3)
        t_breakdown = ft.TextField(label="Price Breakdown / Costing", value=job.get('price_breakdown','') if job else "", multiline=True, min_lines=3)
        
        dd_flag = ft.Dropdown(
            label="Job Label / Priority",
            options=[
                ft.dropdown.Option("0", "Normal"),
                ft.dropdown.Option("1", "🚨 No deposit & suspended (RED)"),
                ft.dropdown.Option("2", "⚠️ No deposit & WIP (ORANGE)"),
                ft.dropdown.Option("3", "⏳ For standby purpose (YELLOW)"),
            ],
            value=str(flag_val)
        )

        def save_click(e):
            if is_readonly: return
            final_flag = int(dd_flag.value) if dd_flag.value else 0

            data = {
                "job_code": t_code.value, "memo_no": t_memo.value,
                "invoice_no": t_invoice.value, "do_no": t_do.value, "billed_by": t_billed.value,
                "customer": t_cust.value, "supervisor": t_pic.value,
                "trailer_type": t_type.value, "price_text": t_price.value, "summary": t_summary.value,
                "notes": t_notes.value, "price_breakdown": t_breakdown.value,
                "flagged": final_flag, "category": category_val 
            }

            if is_new:
                data["status_idx"] = 0
                data["closed"] = 0
                success, err = db.create_job(data, state["user"])
            else:
                success, err = db.update_job(job['id'], data, state["user"], t_code.value)

            if success:
                if err: show_snack(f"Job Saved. Warning: {err}", is_error=True)
                else: show_snack("Job Saved Successfully")
                reload_current_view()
            else:
                show_snack(f"Error: {err}", is_error=True)

        def move_status_click(target_idx):
            updates = {"status_idx": target_idx}
            if target_idx >= 8:
                updates["completed_at"] = get_mys_iso()
                updates["closed"] = 1
            else:
                updates["closed"] = 0
                updates["completed_at"] = None
            
            success, err = db.update_job(job['id'], updates, state["user"], job['job_code'], old_status=job['status_idx'], new_status=target_idx)
            
            if success:
                if err: show_snack(f"Status Updated. Warning: {err}", is_error=True)
                if target_idx == 8: state["last_view_type"] = "archive" 
                elif target_idx == 9: state["last_view_type"] = "deleted"
                else:
                    state["last_view_type"] = "status"
                    state["last_status_idx"] = target_idx
                    state["last_local_filter"] = ""
                    state["scroll_pos"] = 0.0
                reload_current_view()
            else:
                show_snack(f"Error: {err}", is_error=True)

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.Icons.ARROW_BACK, on_click=lambda e: reload_current_view()),
            title=ft.Text(f"{category_val.capitalize()} Details", size=16), 
            bgcolor="blue" if category_val == "new" else "orange", color="white",
            actions=[ft.IconButton(ft.Icons.CHECK, on_click=save_click) if not is_readonly else ft.Container()]
        )

        status_buttons = []
        if not is_new and not is_readonly:
            btns_to_show = STATUSES_NEW if category_val == "new" else STATUSES_USED
            current_s = job.get('status_idx', 0)
            status_buttons.append(ft.Text("Move Status:", weight="bold"))
            row_btns = []
            
            for idx, label in btns_to_show:
                if idx < 8 and idx != current_s: 
                    simple_label = label.split(" - ")[0]
                    row_btns.append(ft.OutlinedButton(simple_label, on_click=lambda e, i=idx: move_status_click(i)))
            
            status_buttons.append(ft.Column(row_btns) if is_mobile else ft.Row(row_btns, wrap=True))
            
            if current_s < 9:
                status_buttons.append(ft.Divider())
                action_row_controls = []
                if current_s == 7: 
                    action_row_controls.append(ft.ElevatedButton("CLOSE (ARCHIVE)", color="white", bgcolor="green", on_click=lambda e: move_status_click(8)))
                
                action_row_controls.append(ft.ElevatedButton("DELETE JOB", color="white", bgcolor="red", on_click=lambda e: move_status_click(9)))
                status_buttons.append(ft.Row(action_row_controls, wrap=True))
        
        page.add(ft.Container(
            content=ft.Column([
                responsive_row([t_code, t_memo]),
                responsive_row([t_invoice, t_do]),
                t_billed, t_cust, t_pic, t_summary, t_type, t_price, 
                dd_flag, 
                ft.Text("Notes & Costing", weight="bold", color="grey"),
                t_notes, t_breakdown,
                ft.Divider(), 
                ft.Column(status_buttons)
            ], scroll=ft.ScrollMode.ADAPTIVE, expand=True, spacing=15), 
            padding=15, expand=True, alignment=ft.alignment.top_center
        ))

    def load_history_view(job_code_filter=None):
        page.clean()
        state["in_details"] = False
        state["history_filter_job"] = job_code_filter
        
        title_str = f"History: {job_code_filter}" if job_code_filter else "Global System History"
        
        leading_icon = ft.IconButton(ft.Icons.ARROW_BACK, on_click=lambda e: handle_back_button()) if job_code_filter else ft.IconButton(ft.Icons.MENU, on_click=safe_open_drawer)

        page.appbar = ft.AppBar(leading=leading_icon, title=ft.Text(title_str, size=14), bgcolor="blue", color="white")
        
        logs = db.fetch_history(job_code_filter)
        lv = ft.ListView(expand=True, padding=10, spacing=5)
        
        if not logs: lv.controls.append(ft.Text("No history found.", text_align="center"))

        for log in logs:
            job_c = log.get('job_code', 'Unknown')
            user = log.get('changed_by', 'System')
            old_s = STATUS_DICT.get(log.get('old_status'), '?')
            
            new_s_id = log.get('new_status')
            if new_s_id == 8: new_s = "Closed"
            elif new_s_id == 9: new_s = "Deleted"
            else: new_s = STATUS_DICT.get(new_s_id, 'Unknown')

            raw_time = str(log.get('changed_at', ''))
            time = raw_time.replace('T', ' ')[:16] if raw_time else ""
            
            details = log.get('details', '')
            if details: msg = details
            elif log.get('old_status') == -1: msg = "Job Created"
            elif log.get('old_status') != log.get('new_status'): msg = f"{old_s} -> {new_s}"
            else: msg = "Edited"
            
            tile = ft.Container(
                content=ft.Column([
                    ft.Row([ft.Text(job_c, weight="bold"), ft.Text(time, size=10, color="grey")], alignment="spaceBetween"),
                    ft.Text(f"{user}: {msg}", size=12)
                ]),
                padding=10, bgcolor=ft.Colors.GREY_100, border_radius=5
            )
            lv.controls.append(tile)
        
        page.add(ft.Container(content=lv, alignment=ft.alignment.top_center, expand=True))

    show_login()

ft.app(target=main, assets_dir="assets")
