import flet as ft
from supabase import create_client, Client
from datetime import datetime, timedelta
import os
import traceback 

# Paste your actual keys here (Strings)
SUPABASE_URL = "https://qqxaujdifamluzlhixvv.supabase.co"
SUPABASE_KEY = "sb_publishable_lVx4h2kOKOB6_zenbJRH_g_GNCt5TgX"

# --- VERSION ---
APP_VERSION = "v1.9.2 (Mobile)"

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
]

STATUSES_USED = [
    (0, "0% - Job Created"),
    (3, "50% - Work In Progress"),
    (6, "100% - Ready For Delivery"),
    (7, "Delivered"),
    (8, "Closed / Archived"),
]

STATUS_DICT = {idx: label for idx, label in STATUSES_NEW}
STATUS_DICT[3] = "50% - Painting / WIP" 

# --- HELPER: MALAYSIAN TIME ---
def get_mys_iso():
    return (datetime.utcnow() + timedelta(hours=8)).isoformat()

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

    def fetch_jobs(self, category="new", status_filter=None, closed=0, search_term=None):
        if not self.client: return []
        try:
            query = self.client.table("memo_system").select("*")
            
            # Filter by Category
            query = query.eq("category", category)

            if search_term:
                or_str = f"job_code.ilike.%{search_term}%,memo_no.ilike.%{search_term}%,do_no.ilike.%{search_term}%,invoice_no.ilike.%{search_term}%,customer.ilike.%{search_term}%,supervisor.ilike.%{search_term}%"
                query = query.or_(or_str)
            else:
                if status_filter == 8: 
                    query = query.eq("status_idx", 8)
                else:
                    query = query.neq("status_idx", 8)
                    if status_filter is not None:
                        query = query.eq("status_idx", status_filter)
            
            res = query.order("updated_at", desc=True).execute()
            return res.data
        except Exception as e:
            return []

    def fetch_history(self):
        if not self.client: return []
        try:
            res = self.client.table("job_history").select("*").order("changed_at", desc=True).limit(50).execute()
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
            # 1. FETCH OLD DATA FIRST
            current_res = self.client.table("memo_system").select("*").eq("id", job_id).execute()
            if not current_res.data: return False, "Job not found"
            old_data = current_res.data[0]

            # 2. PERFORM UPDATE
            new_data["updated_at"] = get_mys_iso()
            self.client.table("memo_system").update(new_data).eq("id", job_id).execute()
            
            # 3. LOGGING LOGIC
            log_msg = None
            log_old_s = old_data['status_idx']
            log_new_s = old_data['status_idx']

            if old_status is not None and new_status is not None and old_status != new_status:
                old_lbl = STATUS_DICT.get(old_status, "?")
                new_lbl = STATUS_DICT.get(new_status, "?")
                old_short = old_lbl.split(" - ")[0]
                new_short = new_lbl.split(" - ")[0]
                if old_status == 8: old_short = "Closed"
                if new_status == 8: new_short = "Closed"
                log_msg = f"{old_short} -> {new_short}"
                log_old_s = old_status
                log_new_s = new_status
            else:
                core_fields = ["customer", "price_text", "memo_no", "invoice_no", "do_no", "billed_by", "job_code"]
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
    # 1. SETUP PAGE BASICS
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 0
    page.title = f"Syaifar's Kanban {APP_VERSION}"
    page.window.width = 390
    page.window.height = 844

    # 2. DEFINE STATE IMMEDIATELY
    state = {
        "user": "",
        "role": "",
        "category": "new",  # 'new' or 'used'
        "last_view_type": "overview", 
        "last_status_idx": None,
        "last_search_term": "",
        "last_local_filter": "",
        "scroll_pos": 0.0,
        "in_details": False 
    }

    # 3. ROBUST BACK HANDLER
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
            print(f"Back Error: {ex}")
            return False


    # 4. REGISTER HANDLER IMMEDIATELY
    page.on_back_button = handle_back_button

    # 5. DESKTOP SIMULATION (Esc Key)
    def on_keyboard(e: ft.KeyboardEvent):
        if e.key == "Escape":
            handle_back_button()
    page.on_keyboard_event = on_keyboard
    
    # 6. FORCE UPDATE TO REGISTER LISTENERS
    page.update()

    # --- HELPERS ---
    def show_snack(msg, is_error=False):
        page.snack_bar = ft.SnackBar(
            content=ft.Text(msg),
            bgcolor=ft.Colors.RED if is_error else ft.Colors.GREEN
        )
        page.snack_bar.open = True
        page.update()

    # --- UI COMPONENTS ---
    loading_container = ft.Container(
        content=ft.Column([
            ft.ProgressRing(),
            ft.Text("Initializing System...", size=16)
        ], horizontal_alignment="center", alignment=ft.MainAxisAlignment.CENTER),
        alignment=ft.alignment.center,
        expand=True
    )
    page.add(loading_container)
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
            
            # Category Switchers
            ft.NavigationDrawerDestination(icon=ft.Icons.CONSTRUCTION, label="New Construction"),
            ft.NavigationDrawerDestination(icon=ft.Icons.CAR_REPAIR, label="Used / Refurb"),
            ft.Divider(thickness=1),
            
            # Views
            ft.NavigationDrawerDestination(icon=ft.Icons.DASHBOARD, label="Overview (Dashboard)"),
            ft.NavigationDrawerDestination(icon=ft.Icons.SEARCH, label="Global Search"),
            ft.Divider(thickness=1),
        ]

        # Dynamic Status Items
        for idx, label in current_status_list:
            if idx < 8: 
                nav_controls.append(ft.NavigationDrawerDestination(icon=ft.Icons.CIRCLE_OUTLINED, label=label))

        # Bottom Items
        nav_controls.extend([
            ft.Divider(thickness=1),
            ft.NavigationDrawerDestination(icon=ft.Icons.ARCHIVE, label="Closed / Archived"),
            ft.NavigationDrawerDestination(icon=ft.Icons.HISTORY, label="History Logs"),
            ft.NavigationDrawerDestination(icon=ft.Icons.LOGOUT, label="Logout"),
            ft.Container(content=ft.Text(APP_VERSION, color="grey", size=12), padding=ft.padding.only(left=20, top=10, bottom=10))
        ])

        return ft.NavigationDrawer(
            controls=nav_controls, 
            on_change=on_nav_change
        )

    def on_nav_change(e):
        try:
            idx = e.control.selected_index
            state["last_local_filter"] = ""
            state["scroll_pos"] = 0.0
            state["in_details"] = False
            
            current_status_list = STATUSES_NEW if state["category"] == "new" else STATUSES_USED
            status_count = len([s for s in current_status_list if s[0] < 8])

            if idx == 0: # Switch to New
                state["category"] = "new"
                state["last_view_type"] = "overview"
                page.drawer = get_drawer()
                load_job_list_view("New Construction Dashboard")
            
            elif idx == 1: # Switch to Used
                state["category"] = "used"
                state["last_view_type"] = "overview"
                page.drawer = get_drawer()
                load_job_list_view("Used / Refurb Dashboard")

            elif idx == 2: # Overview
                state["last_view_type"] = "overview"
                load_job_list_view("Overview Dashboard")

            elif idx == 3: # Search
                show_search_view()

            elif 4 <= idx < (4 + status_count): # Status Filter
                list_idx = idx - 4
                status_idx = current_status_list[list_idx][0]
                state["last_view_type"] = "status"
                state["last_status_idx"] = status_idx
                load_job_list_view(STATUS_DICT.get(status_idx, "Status View"))

            elif idx == (4 + status_count): # Archive
                state["last_view_type"] = "archive"
                state["last_status_idx"] = 8
                load_job_list_view("Archived Jobs")

            elif idx == (4 + status_count + 1): # History
                state["last_view_type"] = "history"
                load_history_view()

            elif idx == (4 + status_count + 2): # Logout
                state["user"] = ""
                show_login()
                return

            if page.drawer:
                page.drawer.open = False
            page.update()
        except Exception as ex:
            print(f"Nav Error: {ex}")

    def reload_current_view():
        view_type = state["last_view_type"]
        state["in_details"] = False 

        if view_type == "overview": load_job_list_view(f"{state['category'].capitalize()} Dashboard")
        elif view_type == "status": load_job_list_view(STATUS_DICT.get(state["last_status_idx"], "Status View"))
        elif view_type == "archive": load_job_list_view("Archived Jobs")
        elif view_type == "search": load_job_list_view(f"Results: {state['last_search_term']}", is_global_search=True)
        elif view_type == "history": load_history_view()
        else: load_job_list_view("Overview Dashboard")

    # --- SCREENS ---
    def show_login():
        page.clean()
        page.appbar = None
        page.drawer = None
        page.floating_action_button = None
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

        # --- LOGIN CENTERED ---
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
                    ], horizontal_alignment="center", alignment=ft.MainAxisAlignment.CENTER),
                    alignment=ft.alignment.center,
                    expand=True,
                    bgcolor=ft.Colors.BLUE_50
                ),
                ft.Container(
                    content=ft.Text(APP_VERSION, color="grey", size=12, weight="bold"),
                    bottom=10,
                    left=10
                )
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

    def load_job_list_view(title, is_global_search=False):
        page.clean()
        state["in_details"] = False
        
        current_status_list = STATUSES_NEW if state["category"] == "new" else STATUSES_USED

        # --- 1. SETUP APPBAR ---
        def refresh_action(e):
            reload_current_view()

        appbar_actions = []
        if state["role"] == "admin":
             appbar_actions.append(ft.IconButton(ft.Icons.ADD, on_click=lambda e: show_job_details(None), tooltip="Create New Job"))
        appbar_actions.append(ft.IconButton(ft.Icons.REFRESH, on_click=refresh_action, tooltip="Refresh"))

        page.appbar = ft.AppBar(
            leading=ft.IconButton(ft.Icons.MENU, on_click=safe_open_drawer), 
            title=ft.Text(title, size=16), 
            bgcolor="blue" if state["category"] == "new" else "orange", 
            color="white", 
            actions=appbar_actions
        )
        page.floating_action_button = None

        # --- 2. FETCH DATA ---
        if is_global_search: 
            jobs = db.fetch_jobs(category=state["category"], search_term=state["last_search_term"])
        elif state["last_view_type"] == "archive": 
            jobs = db.fetch_jobs(category=state["category"], status_filter=8, closed=1)
        elif state["last_view_type"] == "status": 
            jobs = db.fetch_jobs(category=state["category"], status_filter=state["last_status_idx"])
        else: 
            jobs = db.fetch_jobs(category=state["category"], status_filter=None, closed=0)

        # --- 3. DASHBOARD (Special Case) ---
        if state["last_view_type"] == "overview":
            stats = {idx: {'total': 0, 'flagged': 0} for idx, _ in current_status_list if idx < 8}
            for j in jobs:
                s_idx = j.get('status_idx', 0)
                if s_idx in stats:
                    stats[s_idx]['total'] += 1
                    if (j.get('flagged', 0) == 1) or (j.get('flagged') is True): stats[s_idx]['flagged'] += 1

            dashboard_col = ft.Column(spacing=10, scroll=ft.ScrollMode.ADAPTIVE, expand=True)
            dashboard_col.controls.append(ft.Container(content=ft.Text(f"Active {state['category'].capitalize()} Jobs: {len(jobs)}", size=20, weight="bold", color="blue"), padding=10))

            def open_status_view(idx):
                state["last_view_type"] = "status"
                state["last_status_idx"] = idx
                state["last_local_filter"] = "" 
                state["scroll_pos"] = 0.0
                reload_current_view()

            for idx, label in current_status_list:
                if idx >= 8: continue 
                total = stats.get(idx, {}).get('total', 0)
                flagged = stats.get(idx, {}).get('flagged', 0)
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
            page.add(ft.Container(content=dashboard_col, padding=10, expand=True))
            return 

        # --- 4. LIST VIEW LOGIC ---
        def on_scroll_list(e: ft.OnScrollEvent): state["scroll_pos"] = e.pixels
        list_container = ft.ListView(expand=True, spacing=10, on_scroll=on_scroll_list)

        def draw_cards(job_list):
            list_container.controls.clear()
            if not job_list: 
                list_container.controls.append(ft.Text("No jobs found.", text_align="center", color="grey"))
                return

            for job in job_list:
                is_flagged = (job.get('flagged', 0) == 1) or (job.get('flagged') is True)
                card_bg = ft.Colors.RED_400 if is_flagged else "white"
                text_color = "white" if is_flagged else "black"
                sub_text_color = "white70" if is_flagged else "grey"
                icon_color = "white" if is_flagged else "blue"

                memo_val = job.get('memo_no')
                memo_display = f"Memo: {memo_val}" if memo_val else "Memo: -"

                inv_val = job.get('invoice_no', '')
                bill_val = job.get('billed_by', '')
                do_val = job.get('do_no', '')
                
                row3_text = f"Inv: {inv_val if inv_val else '-'}  |  DO: {do_val if do_val else '-'}"
                row4_text = f"Bill: {bill_val if bill_val else '-'}"

                raw_created = str(job.get('created_at', ''))
                raw_updated = str(job.get('updated_at', ''))
                nice_created = raw_created.replace('T', ' ')[:16] if raw_created else ""
                nice_updated = raw_updated.replace('T', ' ')[:16] if raw_updated else ""
                
                date_col_controls = [ft.Text(f"Cr: {nice_created}", size=11, color=sub_text_color)]
                if nice_updated and nice_updated != nice_created:
                    date_col_controls.append(ft.Text(f"Upd: {nice_updated}", size=11, color=sub_text_color))

                card_content = ft.Container(
                    content=ft.Column([
                        ft.Row([
                            ft.Text(job['job_code'], weight="bold", size=16, color=text_color),
                            ft.Column(date_col_controls, alignment="end", spacing=0) 
                        ], alignment="spaceBetween"),

                        ft.Text(memo_display, weight="bold", size=14, color=ft.Colors.BLUE_200 if is_flagged else ft.Colors.BLUE),
                        ft.Text(row3_text, size=12, color=text_color),
                        ft.Text(row4_text, size=12, color=sub_text_color),

                        ft.Divider(height=5, color="transparent"),
                        ft.Text(f"Cust: {job.get('customer', '-')}", size=14, color=text_color),
                        ft.Text(f"Type: {job.get('trailer_type', '-')}", size=12, color=sub_text_color),
                        ft.Text(f"Price: {job.get('price_text', '-')}", size=12, color=sub_text_color),
                        ft.Text(f"PIC: {job.get('supervisor', '-')}", size=12, color=sub_text_color),
                        ft.Divider(height=5, color="transparent"),
                        ft.Text(f"{job.get('summary', '')}", size=12, italic=True, color=text_color, max_lines=2, overflow="ellipsis"),
                        ft.Row([
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=14, color=icon_color),
                            ft.Text(f"{STATUS_DICT.get(job['status_idx'], 'Unknown')}", size=12, color=icon_color)
                        ], alignment="end")
                    ]),
                    padding=15,
                    on_click=lambda e, j=job: show_job_details(j)
                )
                list_container.controls.append(ft.Card(content=card_content, color=card_bg))
            page.update()

        # --- 5. SEARCH BAR SELECTION ---
        top_bar = None
        
        if is_global_search:
            search_input = ft.TextField(
                value=state["last_search_term"],
                label="Search Database",
                hint_text="Job Code / Memo / DO / Inv...",
                expand=True,      
                height=50,
                text_size=14,
                content_padding=10
            )

            def run_search_trigger(e):
                new_term = search_input.value
                if not new_term: return
                state["last_search_term"] = new_term
                load_job_list_view(f"Results: {new_term}", is_global_search=True)

            search_input.on_submit = run_search_trigger

            search_btn = ft.IconButton(
                icon=ft.Icons.SEARCH, 
                icon_color="blue",
                tooltip="Click to Search",
                on_click=run_search_trigger
            )

            top_bar = ft.Row([search_input, search_btn], spacing=5)
            draw_cards(jobs)

        else:
            def run_local_filter(e):
                filter_text = e.control.value.lower()
                state["last_local_filter"] = filter_text
                
                visible_jobs = []
                if not filter_text: 
                    visible_jobs = jobs
                else:
                    for j in jobs:
                        full_text = f"{j['job_code']} {j.get('memo_no','')} {j.get('do_no','')} {j.get('invoice_no','')} {j.get('customer','')} {j.get('supervisor','')} {j.get('summary','')}".lower()
                        if filter_text in full_text: visible_jobs.append(j)
                draw_cards(visible_jobs)

            top_bar = ft.TextField(
                value=state.get("last_local_filter", ""), 
                hint_text=f"Filter these {len(jobs)} jobs...", 
                prefix_icon=ft.Icons.FILTER_LIST, 
                height=50, 
                text_size=14, 
                content_padding=10,
                on_change=run_local_filter
            )
            run_local_filter(ft.ControlEvent(target="", name="change", data=state.get("last_local_filter", ""), control=top_bar, page=page))

        page.add(ft.Container(
            content=ft.Column([
                ft.Container(content=top_bar, padding=10), 
                list_container
            ], spacing=0, expand=True), 
            expand=True
        ))
        
        if state["scroll_pos"] > 0: 
            list_container.scroll_to(offset=state["scroll_pos"], duration=0)

    def show_job_details(job):
        page.clean()
        state["in_details"] = True 

        is_new = job is None
        is_readonly = state["role"] != "admin"

        current_flag_val = job.get('flagged', 0) if job else 0
        flag_bool = True if (current_flag_val == 1 or current_flag_val is True) else False

        category_val = job.get('category', state["category"]) if job else state["category"]
        
        id_label = "Job Code" if category_val == "new" else "Unit ID / Reg No"
        code_val = job['job_code'] if job else f"{'JOB' if category_val=='new' else 'USED'}-{int(datetime.utcnow().timestamp())}"
        
        # UPDATED: Editable Job Code
        t_code = ft.TextField(label=id_label, value=code_val, disabled=False)
        t_memo = ft.TextField(label="Memo NO", value=job.get('memo_no','') if job else "")
        
        t_invoice = ft.TextField(label="Invoice No", value=job.get('invoice_no','') if job else "")
        t_do = ft.TextField(label="DO Number", value=job.get('do_no','') if job else "")
        t_billed = ft.TextField(label="Billed By", value=job.get('billed_by','') if job else "")

        t_cust = ft.TextField(label="Customer", value=job.get('customer','') if job else "")
        t_pic = ft.TextField(label="PIC / Supervisor", value=job.get('supervisor','') if job else "")
        t_type = ft.TextField(label="Trailer Type", value=job.get('trailer_type','') if job else "")
        t_price = ft.TextField(label="Price (Total)", value=job.get('price_text','') if job else "")
        t_summary = ft.TextField(label="Summary", value=job.get('summary','') if job else "")
        t_notes = ft.TextField(label="Production Notes", value=job.get('notes','') if job else "", multiline=True, min_lines=3)
        t_breakdown = ft.TextField(label="Price Breakdown / Costing", value=job.get('price_breakdown','') if job else "", multiline=True, min_lines=3)

        c_flag = ft.Checkbox(label="Flag Urgent", value=flag_bool)

        def save_click(e):
            if is_readonly: return
            final_flag = 1 if c_flag.value else 0

            data = {
                "job_code": t_code.value, 
                "memo_no": t_memo.value, 
                "invoice_no": t_invoice.value,
                "do_no": t_do.value,
                "billed_by": t_billed.value,
                "customer": t_cust.value, "supervisor": t_pic.value,
                "trailer_type": t_type.value, "price_text": t_price.value, "summary": t_summary.value,
                "notes": t_notes.value, 
                "price_breakdown": t_breakdown.value,
                "flagged": final_flag,
                "category": category_val
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
            if target_idx == 8:
                updates["completed_at"] = get_mys_iso()
                updates["closed"] = 1
            else:
                updates["closed"] = 0
                updates["completed_at"] = None
            
            success, err = db.update_job(job['id'], updates, state["user"], job['job_code'], old_status=job['status_idx'], new_status=target_idx)
            
            if success:
                if err: show_snack(f"Status Updated. Warning: {err}", is_error=True)
                if target_idx == 8: state["last_view_type"] = "archive"
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
            title=ft.Text(f"{category_val.capitalize()} Details"), 
            bgcolor="blue" if category_val == "new" else "orange", 
            color="white",
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
            status_buttons.append(ft.Row(row_btns, wrap=True))
            if current_s == 7:
                status_buttons.append(ft.Divider())
                status_buttons.append(ft.ElevatedButton("CLOSE JOB (ARCHIVE)", color="white", bgcolor="green", on_click=lambda e: move_status_click(8)))
        
        page.add(ft.Container(
            content=ft.Column([
                # UPDATED LAYOUT: Vertical Stack
                t_code, 
                t_memo, 
                t_invoice, 
                t_do, 
                t_billed,
                t_cust, t_pic, t_summary, t_type, t_price, 
                c_flag, 
                ft.Text("Notes & Costing", weight="bold", color="grey"),
                t_notes, 
                t_breakdown, 
                ft.Divider(), 
                ft.Column(status_buttons)
            ], scroll=ft.ScrollMode.ADAPTIVE, expand=True), 
            padding=15, 
            expand=True
        ))

    def load_history_view():
        page.clean()
        state["in_details"] = False
        
        page.appbar = ft.AppBar(leading=ft.IconButton(ft.Icons.MENU, on_click=safe_open_drawer), title=ft.Text("System History"), bgcolor="blue", color="white")
        
        logs = db.fetch_history()
        lv = ft.ListView(expand=True, padding=10, spacing=5)
        
        if not logs: lv.controls.append(ft.Text("No history found.", text_align="center"))

        for log in logs:
            job_c = log.get('job_code', 'Unknown')
            user = log.get('changed_by', 'System')
            old_s = STATUS_DICT.get(log.get('old_status'), '?')
            new_s = STATUS_DICT.get(log.get('new_status'), 'Closed')
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
        page.add(lv)

    show_login()

ft.app(target=main, view=ft.AppView.FLET_APP)
