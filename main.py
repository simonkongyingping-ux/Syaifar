import flet as ft
from supabase import create_client, Client
from datetime import datetime, timedelta
import os

# Paste your actual keys here (Strings)
SUPABASE_URL = "https://qqxaujdifamluzlhixvv.supabase.co"
SUPABASE_KEY = "sb_publishable_lVx4h2kOKOB6_zenbJRH_g_GNCt5TgX"

STATUSES = [
    (0, "0% - Job Created"),
    (1, "10% - Chassis Fab Started"),
    (2, "30% - Body Fab Started"),
    (3, "50% - Painting Started"),
    (4, "80% - Fittings Started"),
    (5, "90% - PUS / JPJ"),
    (6, "100% - Ready For Delivery"),
    (7, "Closed / Archived"),
]
STATUS_DICT = {idx: label for idx, label in STATUSES}

# --- HELPER: MALAYSIAN TIME ---
def get_mys_iso():
    # UTC + 8 Hours
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

    def fetch_jobs(self, status_filter=None, closed=0, search_term=None):
        if not self.client: return []
        try:
            query = self.client.table("memo_system").select("*")
            
            if search_term:
                or_str = f"job_code.ilike.%{search_term}%,customer.ilike.%{search_term}%,supervisor.ilike.%{search_term}%,summary.ilike.%{search_term}%"
                query = query.or_(or_str)
            else:
                if status_filter == 7:
                    query = query.eq("status_idx", 7)
                else:
                    query = query.neq("status_idx", 7)
                    if status_filter is not None:
                        query = query.eq("status_idx", status_filter)
            
            res = query.order("updated_at", desc=True).execute()
            return res.data
        except Exception as e:
            print(f"DB Error: {e}")
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
                if old_status == 7: old_short = "Closed"
                if new_status == 7: new_short = "Closed"
                log_msg = f"{old_short} -> {new_short}"
                log_old_s = old_status
                log_new_s = new_status
            else:
                core_fields = ["customer", "supervisor", "trailer_type", "price_text"]
                core_changed = any(str(old_data.get(f,'')) != str(new_data.get(f,'')) for f in core_fields if f in new_data)
                
                if core_changed: log_msg = "Details Updated"
                elif "summary" in new_data and str(old_data.get("summary",'')) != str(new_data["summary"]): log_msg = "Summary Updated"
                elif "notes" in new_data and str(old_data.get("notes",'')) != str(new_data["notes"]): log_msg = "Notes Updated"

            if log_msg:
                hist_ok, hist_err = self.log_history(job_id, job_code, log_old_s, log_new_s, user, log_msg)
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
    page.theme_mode = ft.ThemeMode.LIGHT
    page.padding = 0
    page.title = "Sibu Kanban"
    page.window.width = 390
    page.window.height = 844

    def show_snack(msg, is_error=False):
        page.snack_bar = ft.SnackBar(
            content=ft.Text(msg),
            bgcolor=ft.Colors.RED if is_error else ft.Colors.GREEN
        )
        page.snack_bar.open = True
        page.update()

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

    state = {
        "user": "",
        "role": "",
        "last_view_type": "overview", 
        "last_status_idx": None,
        "last_search_term": "",
        "last_local_filter": "",
        "scroll_pos": 0.0,
        "in_details": False # <-- CRITICAL FLAG
    }

    # --- HIERARCHY LOGIC WITH DEBUG ---
    def handle_back_button(view=None): 
        # 1. If we are in DETAILS screen, close it and go back to list
        if state["in_details"] is True:
            # DEBUG: Uncomment the line below to see visual proof on phone
            # show_snack("Back: Closing Details")
            state["in_details"] = False
            reload_current_view()
            page.update() 
            return True
        
        # 2. If we are in ANY LIST (Archive, Search, History, Status), go to DASHBOARD
        if state["user"] != "" and state["last_view_type"] != "overview":
            # DEBUG: Uncomment the line below to see visual proof on phone
            # show_snack("Back: Returning to Dashboard")
            state["last_view_type"] = "overview"
            load_job_list_view("Overview Dashboard")
            page.update() 
            return True

        # 3. If on Dashboard, let Android minimize
        # show_snack("Back: Minimizing App") # Visual proof
        return False

    # --- DESKTOP KEYBOARD SIMULATOR ---
    def on_keyboard(e: ft.KeyboardEvent):
        if e.key == "Escape":
            handle_back_button(None)

    page.on_back_button = handle_back_button # For Android
    page.on_keyboard_event = on_keyboard     # For Desktop Testing

    def safe_open_drawer(e):
        page.drawer.open = True
        page.update()

    def get_drawer():
        nav_items = [
            ft.NavigationDrawerDestination(icon=ft.Icons.DASHBOARD, label="Overview (Dashboard)"),
            ft.NavigationDrawerDestination(icon=ft.Icons.SEARCH, label="Global Search"),
        ]
        for idx, label in STATUSES:
            if idx < 7: nav_items.append(ft.NavigationDrawerDestination(icon=ft.Icons.CIRCLE_OUTLINED, label=label))

        nav_items.extend([
            ft.NavigationDrawerDestination(icon=ft.Icons.ARCHIVE, label="Closed / Archived"),
            ft.NavigationDrawerDestination(icon=ft.Icons.HISTORY, label="History Logs"),
            ft.NavigationDrawerDestination(icon=ft.Icons.LOGOUT, label="Logout"),
        ])

        return ft.NavigationDrawer(
            controls=[
                ft.Container(height=20),
                ft.Text("  Select View", weight="bold", size=20, color="blue"),
                ft.Container(height=10),
                nav_items[0], ft.Divider(thickness=1),
                nav_items[1], ft.Divider(thickness=1),
            ] + nav_items[2:9] + [ft.Divider(thickness=1)] + nav_items[9:], 
            on_change=on_nav_change
        )

    def on_nav_change(e):
        try:
            idx = e.control.selected_index
            state["last_local_filter"] = ""
            state["scroll_pos"] = 0.0
            state["in_details"] = False

            if idx == 0: 
                state["last_view_type"] = "overview"
                load_job_list_view("Overview Dashboard")
            elif idx == 1: 
                show_search_view()
            elif 2 <= idx <= 8: 
                status_idx = STATUSES[idx - 2][0]
                state["last_view_type"] = "status"
                state["last_status_idx"] = status_idx
                load_job_list_view(STATUS_DICT[status_idx])
            elif idx == 9: 
                state["last_view_type"] = "archive"
                state["last_status_idx"] = 7
                load_job_list_view("Archived Jobs")
            elif idx == 10: 
                state["last_view_type"] = "history"
                load_history_view()
            elif idx == 11: 
                state["user"] = ""
                show_login()
            page.drawer.open = False
            page.update()
        except Exception as ex:
            print(f"Nav Error: {ex}")

    def reload_current_view():
        view_type = state["last_view_type"]
        state["in_details"] = False 

        if view_type == "overview": load_job_list_view("Overview Dashboard")
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

        # --- LOGIN CENTERED (SPACER METHOD) ---
        page.add(
            ft.Container(
                content=ft.Column([
                    ft.Container(expand=1), # Top Spacer
                    ft.Column([
                        ft.Icon(ft.Icons.DIRECTIONS_CAR, size=60, color="blue"),
                        ft.Text("Sibu Kanban", size=24, weight="bold"),
                        ft.Text("Job Management System", size=16),
                        ft.Divider(height=20, color="transparent"),
                        user_dropdown, pass_in,
                        ft.ElevatedButton("Login", on_click=attempt_login, bgcolor="blue", color="white"),
                        status_lbl
                    ], horizontal_alignment="center"),
                    ft.Container(expand=1), # Bottom Spacer
                ], horizontal_alignment="center"),
                expand=True,
                bgcolor=ft.Colors.BLUE_50
            )
        )

    def show_search_view():
        page.clean()
        state["last_local_filter"] = "" 
        state["in_details"] = False
        
        search_box = ft.TextField(label="Search (Code, Cust, PIC)", expand=True, autofocus=True)
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
        
        def open_status_view(idx):
            state["last_view_type"] = "status"
            state["last_status_idx"] = idx
            state["last_local_filter"] = "" 
            state["scroll_pos"] = 0.0
            reload_current_view()

        appbar_actions = []
        if state["role"] == "admin":
             appbar_actions.append(ft.IconButton(ft.Icons.ADD, on_click=lambda e: show_job_details(None), tooltip="Create New Job"))
        appbar_actions.append(ft.IconButton(ft.Icons.REFRESH, on_click=lambda e: reload_current_view(), tooltip="Refresh"))

        page.appbar = ft.AppBar(leading=ft.IconButton(ft.Icons.MENU, on_click=safe_open_drawer), title=ft.Text(title, size=16), bgcolor="blue", color="white", actions=appbar_actions)
        page.floating_action_button = None

        if is_global_search: jobs = db.fetch_jobs(search_term=state["last_search_term"])
        elif state["last_view_type"] == "archive": jobs = db.fetch_jobs(status_filter=7, closed=1)
        elif state["last_view_type"] == "status": jobs = db.fetch_jobs(status_filter=state["last_status_idx"])
        else: jobs = db.fetch_jobs(status_filter=None, closed=0)

        if state["last_view_type"] == "overview":
            stats = {idx: {'total': 0, 'flagged': 0} for idx, _ in STATUSES if idx < 7}
            for j in jobs:
                s_idx = j.get('status_idx', 0)
                if s_idx in stats:
                    stats[s_idx]['total'] += 1
                    if (j.get('flagged', 0) == 1) or (j.get('flagged') is True): stats[s_idx]['flagged'] += 1

            dashboard_col = ft.Column(spacing=10, scroll=ft.ScrollMode.ADAPTIVE, expand=True)
            dashboard_col.controls.append(ft.Container(content=ft.Text(f"Total Active Jobs: {len(jobs)}", size=20, weight="bold", color="blue"), padding=10))

            for idx, label in STATUSES:
                if idx >= 7: continue 
                total = stats[idx]['total']
                flagged = stats[idx]['flagged']
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

        local_search_box = ft.TextField(value=state.get("last_local_filter", ""), hint_text=f"Filter these {len(jobs)} jobs...", prefix_icon=ft.Icons.SEARCH, height=40, text_size=14, content_padding=10)
        
        def on_scroll_list(e: ft.OnScrollEvent): state["scroll_pos"] = e.pixels
        list_container = ft.ListView(expand=True, spacing=10, on_scroll=on_scroll_list)

        def render_jobs(filter_text=""):
            state["last_local_filter"] = filter_text
            list_container.controls.clear()
            filter_text = filter_text.lower()
            visible_jobs = []
            if not filter_text: visible_jobs = jobs
            else:
                for j in jobs:
                    full_text = f"{j['job_code']} {j.get('customer','')} {j.get('supervisor','')} {j.get('summary','')}".lower()
                    if filter_text in full_text: visible_jobs.append(j)

            if not visible_jobs: list_container.controls.append(ft.Text("No jobs match filter.", text_align="center"))
            
            for job in visible_jobs:
                is_flagged = (job.get('flagged', 0) == 1) or (job.get('flagged') is True)
                card_bg = ft.Colors.RED_400 if is_flagged else "white"
                text_color = "white" if is_flagged else "black"
                sub_text_color = "white70" if is_flagged else "grey"
                icon_color = "white" if is_flagged else "blue"

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

        local_search_box.on_change = lambda e: render_jobs(e.control.value)
        render_jobs(local_search_box.value)
        page.add(ft.Container(content=ft.Column([ft.Container(content=local_search_box, padding=10), list_container], spacing=0, expand=True), expand=True))
        if state["scroll_pos"] > 0: list_container.scroll_to(offset=state["scroll_pos"], duration=0)

    def show_job_details(job):
        page.clean()
        state["in_details"] = True 

        is_new = job is None
        is_readonly = state["role"] != "admin"

        current_flag_val = job.get('flagged', 0) if job else 0
        flag_bool = True if (current_flag_val == 1 or current_flag_val is True) else False

        code_val = job['job_code'] if job else f"JOB-{int(datetime.utcnow().timestamp())}"
        t_code = ft.TextField(label="Job Code", value=code_val, disabled=(not is_new))
        t_cust = ft.TextField(label="Customer", value=job.get('customer','') if job else "")
        t_pic = ft.TextField(label="PIC / Supervisor", value=job.get('supervisor','') if job else "")
        t_type = ft.TextField(label="Trailer Type", value=job.get('trailer_type','') if job else "")
        t_price = ft.TextField(label="Price", value=job.get('price_text','') if job else "")
        t_summary = ft.TextField(label="Summary", value=job.get('summary','') if job else "")
        t_notes = ft.TextField(label="Notes", value=job.get('notes','') if job else "", multiline=True, min_lines=3)
        c_flag = ft.Checkbox(label="Flag Urgent", value=flag_bool)

        def save_click(e):
            if is_readonly: return
            final_flag = 1 if c_flag.value else 0

            data = {
                "job_code": t_code.value, "customer": t_cust.value, "supervisor": t_pic.value,
                "trailer_type": t_type.value, "price_text": t_price.value, "summary": t_summary.value,
                "notes": t_notes.value, "flagged": final_flag
            }

            if is_new:
                data["status_idx"] = 0
                data["closed"] = 0
                success, err = db.create_job(data, state["user"])
            else:
                success, err = db.update_job(job['id'], data, state["user"], job['job_code'])

            if success:
                if err: show_snack(f"Job Saved. Warning: {err}", is_error=True)
                else: show_snack("Job Saved Successfully")
                reload_current_view()
            else:
                show_snack(f"Error: {err}", is_error=True)

        def move_status_click(target_idx):
            updates = {"status_idx": target_idx}
            if target_idx == 7:
                updates["completed_at"] = get_mys_iso()
                updates["closed"] = 1
            else:
                updates["closed"] = 0
                updates["completed_at"] = None
            
            success, err = db.update_job(job['id'], updates, state["user"], job['job_code'], old_status=job['status_idx'], new_status=target_idx)
            
            if success:
                if err: show_snack(f"Status Updated. Warning: {err}", is_error=True)
                if target_idx == 7: state["last_view_type"] = "archive"
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
            title=ft.Text("Job Details"), bgcolor="blue", color="white",
            actions=[ft.IconButton(ft.Icons.CHECK, on_click=save_click) if not is_readonly else ft.Container()]
        )

        status_buttons = []
        if not is_new and not is_readonly:
            current_s = job.get('status_idx', 0)
            status_buttons.append(ft.Text("Move Status:", weight="bold"))
            row_btns = []
            for idx, label in STATUSES:
                if idx < 7 and idx != current_s:
                    row_btns.append(ft.OutlinedButton(label, on_click=lambda e, i=idx: move_status_click(i)))
            status_buttons.append(ft.Row(row_btns, wrap=True))
            if current_s == 6:
                status_buttons.append(ft.Divider())
                status_buttons.append(ft.ElevatedButton("CLOSE JOB (ARCHIVE)", color="white", bgcolor="green", on_click=lambda e: move_status_click(7)))
            
        page.add(ft.Container(content=ft.Column([t_code, t_cust, t_pic, t_summary, t_type, t_price, c_flag, t_notes, ft.Divider(), ft.Column(status_buttons)], scroll=ft.ScrollMode.ADAPTIVE, expand=True), padding=15, expand=True))

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
            
            # --- DISPLAY LOGIC ---
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
