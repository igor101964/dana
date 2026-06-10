/*
 * dana — Data Analysis Viewer
 * C/GTK4 + WebKitGTK6 + Python worker
 * AI analysis via mshell IPC
 */

#define _GNU_SOURCE
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"

#include <gtk/gtk.h>
#include <webkitgtk-6.0/webkit/webkit.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/wait.h>

/* ── Config ─────────────────────────────────────────────────────────── */
#ifndef DANA_WORKER_PATH
#define DANA_WORKER_PATH "/home/igor/dana/dana_worker.py"
#endif
#ifndef DANA_LAST_DATA_PATH
#define DANA_LAST_DATA_PATH "/home/igor/dana/last_data.json"
#endif
#define DANA_WORKER DANA_WORKER_PATH
#define LAST_DATA   DANA_LAST_DATA_PATH
#define APP_TITLE    "dana \xe2\x80\x94 Data Analysis Viewer"
#define APP_W        1200
#define APP_H        780
#define MSHELLRC     "/.mshellrc"
#define MODEL_NAME_MAX 192
#define VENDOR_MAX      64

/* ── Model names ─────────────────────────────────────────────────────── */
static char g_model_name[3][MODEL_NAME_MAX];
static char g_model_short[3][48];
static int  g_current_slot = 1;

static int extract_value(const char *line, const char *key,
                          char *out, int outlen) {
    while (*line == ' ' || *line == '\t') line++;
    if (*line == '#') return 0;
    if (strncmp(line, "export ", 7) == 0) line += 7;
    while (*line == ' ' || *line == '\t') line++;
    size_t klen = strlen(key);
    if (strncmp(line, key, klen) != 0 || line[klen] != '=') return 0;
    const char *p = line + klen + 1;
    char quote = 0;
    if (*p == '\'' || *p == '"') quote = *p++;
    int i = 0;
    while (*p && i < outlen - 1) {
        if (quote && *p == quote) break;
        if (!quote && (*p == '\n' || *p == '\r' || *p == '#')) break;
        out[i++] = *p++;
    }
    out[i] = '\0';
    while (i > 0 && (out[i-1] == ' ' || out[i-1] == '\t')) out[--i] = '\0';
    return i > 0;
}

static void load_model_names(void) {
    for (int s = 0; s < 3; s++) {
        snprintf(g_model_name[s],  MODEL_NAME_MAX, "Model %d", s+1);
        snprintf(g_model_short[s], sizeof(g_model_short[0]),
                 "%d: Model %d", s+1, s+1);
    }
    const char *home = getenv("HOME");
    if (!home) return;
    char path[512];
    snprintf(path, sizeof(path), "%s%s", home, MSHELLRC);
    FILE *f = fopen(path, "r");
    if (!f) return;
    char sm[3][MODEL_NAME_MAX], sv[3][VENDOR_MAX];
    int  sa[3] = {0,0,0};
    memset(sm, 0, sizeof(sm));
    memset(sv, 0, sizeof(sv));
    char line[1024];
    while (fgets(line, (int)sizeof(line), f)) {
        char val[MODEL_NAME_MAX];
        for (int s = 1; s <= 3; s++) {
            char key[40];
            snprintf(key, sizeof(key), "OLLAMA%d_MODEL", s);
            if (extract_value(line, key, val, (int)sizeof(val))) {
                strncpy(sm[s-1], val, MODEL_NAME_MAX-1); sa[s-1] = 1;
            }
            snprintf(key, sizeof(key), "OLLAMA%d_VENDOR", s);
            if (extract_value(line, key, val, VENDOR_MAX))
                strncpy(sv[s-1], val, VENDOR_MAX-1);
        }
    }
    fclose(f);
    for (int s = 0; s < 3; s++) {
        if (!sa[s]) continue;
        if (sv[s][0])
            snprintf(g_model_name[s], MODEL_NAME_MAX,
                     "%.60s / %.120s", sv[s], sm[s]);
        else
            snprintf(g_model_name[s], MODEL_NAME_MAX, "%.185s", sm[s]);
        const char *sl = strrchr(sm[s], '/');
        const char *sh = sl ? sl+1 : sm[s];
        snprintf(g_model_short[s], sizeof(g_model_short[0]),
                 "%d: %.40s", s+1, sh);
    }
}

/* ── IPC ─────────────────────────────────────────────────────────────── */
static int g_ipc_fd_write = -1;
static int g_ipc_fd_read  = -1;
static int g_ipc_seq      = 1;

static void ipc_connect(void) {
    if (g_ipc_fd_write >= 0) return;
    const char *ps = getenv("MSHELL_IPC_PID");
    if (!ps) return;
    int pid = atoi(ps);
    char tx[64], rx[64];
    snprintf(tx, sizeof(tx), "/tmp/mide_to_msh_%d", pid);
    snprintf(rx, sizeof(rx), "/tmp/msh_to_mide_%d", pid);
    g_ipc_fd_write = open(tx, O_WRONLY | O_NONBLOCK);
    g_ipc_fd_read  = open(rx, O_RDONLY | O_NONBLOCK);
}

static void ipc_send_raw(const char *json) {
    if (g_ipc_fd_write < 0) return;
    /* write in 4KB chunks to handle large prompts over pipe */
    const char *p = json;
    size_t rem = strlen(json);
    while (rem > 0) {
        size_t chunk = rem < 4096 ? rem : 4096;
        ssize_t w = write(g_ipc_fd_write, p, chunk);
        if (w <= 0) break;
        p += w; rem -= (size_t)w;
    }
    (void)write(g_ipc_fd_write, "\n", 1);
}

static void ipc_set_provider(int slot) {
    char json[128];
    snprintf(json, sizeof(json),
        "{\"v\":1,\"id\":\"%d\",\"cmd\":31,\"args\":\"%d\"}",
        g_ipc_seq++, slot);
    ipc_send_raw(json);
}

/* JSON escape — malloc, caller frees */
static char *json_escape(const char *s) {
    size_t len = strlen(s);
    char *out = malloc(len * 4 + 4);
    if (!out) return NULL;
    size_t oi = 0;
    for (size_t i = 0; i < len; i++) {
        if      (s[i] == '"')  { out[oi++] = '\\'; out[oi++] = '"';  }
        else if (s[i] == '\\') { out[oi++] = '\\'; out[oi++] = '\\'; }
        else if (s[i] == '\n') { out[oi++] = '\\'; out[oi++] = 'n';  }
        else if (s[i] == '\r') {}
        else if (s[i] == '\t') { out[oi++] = '\\'; out[oi++] = 't';  }
        else                   { out[oi++] = s[i]; }
    }
    out[oi] = '\0';
    return out;
}

static int json_unescape(const char *src, char *dst, int maxlen) {
    int di = 0;
    while (*src && di < maxlen-1) {
        if (*src == '\\' && *(src+1)) {
            src++;
            if (*src == 'n')       { dst[di++] = '\n'; }
            else if (*src == 't')  { dst[di++] = '\t'; }
            else if (*src == '"')  { dst[di++] = '"';  }
            else if (*src == '\\') { dst[di++] = '\\'; }
            else if (*src == 'r')  { dst[di++] = '\r'; }
            else if (*src == 'u' && di < maxlen-4) {
                /* \uXXXX — decode to UTF-8 */
                char hex[5] = {0};
                int ok = 1;
                for (int i = 0; i < 4; i++) {
                    if (*(src+1+i) == '\0') { ok=0; break; }
                    hex[i] = *(src+1+i);
                }
                if (ok) {
                    unsigned int cp = (unsigned int)strtol(hex, NULL, 16);
                    src += 4; /* skip uXXXX */
                    if (cp < 0x80) {
                        dst[di++] = (char)cp;
                    } else if (cp < 0x800) {
                        dst[di++] = (char)(0xC0 | (cp >> 6));
                        dst[di++] = (char)(0x80 | (cp & 0x3F));
                    } else {
                        dst[di++] = (char)(0xE0 | (cp >> 12));
                        dst[di++] = (char)(0x80 | ((cp >> 6) & 0x3F));
                        dst[di++] = (char)(0x80 | (cp & 0x3F));
                    }
                }
            } else { dst[di++] = *src; }
        } else if (*src == '"') { break;
        } else { dst[di++] = *src; }
        src++;
    }
    dst[di] = '\0';
    return di;
}

/* ── Language list ──────────────────────────────────────────────────── */
static const char *LANGUAGES[] = {
    "English","Russian","Chinese","Japanese","Spanish",
    "French","German","Italian","Portuguese","Arabic",
    "Korean","Hindi","Turkish","Polish","Dutch", NULL
};
#define N_LANGS 15

/* ── State ───────────────────────────────────────────────────────────── */
typedef struct { char *query; char *html; char *errmsg; } WorkCtx;
static gboolean  g_busy         = FALSE;
static gboolean  g_dark_mode    = TRUE;
static gboolean  g_ai_enabled   = FALSE;
static int       g_lang_idx     = 0;   /* 0=English */
static gboolean  g_imperial     = FALSE; /* FALSE=Metric */
static GtkWidget *g_lang_dd     = NULL;
static GtkWidget *g_units_switch= NULL;
static char      g_last_query[512] = "";
static char     *g_ai_accum     = NULL;
static size_t    g_ai_accum_sz  = 0;
static int       g_ai_accum_len = 0;
static guint     g_ai_poll_timer = 0;

/* ── Widgets ─────────────────────────────────────────────────────────── */
static GtkWidget      *g_window          = NULL;
static GtkWidget      *g_entry           = NULL;
static GtkWidget      *g_btn_fetch       = NULL;
static GtkWidget      *g_btn_help        = NULL;
static GtkWidget      *g_spinner         = NULL;
static GtkWidget      *g_statusbar       = NULL;
static GtkWidget      *g_btn_model[3];
static GtkWidget      *g_label_model     = NULL;
static GtkWidget      *g_theme_switch    = NULL;
static GtkWidget      *g_ai_switch       = NULL;
static GtkWidget      *g_analysis_panel  = NULL;
static GtkWidget      *g_analysis_text   = NULL;
static GtkWidget      *g_btn_analyze     = NULL;
static WebKitWebView  *g_webview         = NULL;
static GtkCssProvider *g_css             = NULL;

/* ── CSS ─────────────────────────────────────────────────────────────── */
static void apply_css(void) {
    const char *bg      = g_dark_mode ? "#1e1e2e" : "#f4f4f5";
    const char *fg      = g_dark_mode ? "#cdd6f4" : "#18181b";
    const char *ebg     = g_dark_mode ? "#313244" : "#ffffff";
    const char *ebd     = g_dark_mode ? "#45475a" : "#d4d4d8";
    const char *bbg     = g_dark_mode ? "#313244" : "#e4e4e7";
    const char *bbd     = g_dark_mode ? "#45475a" : "#a1a1aa";
    const char *bhov    = g_dark_mode ? "#45475a" : "#d4d4d8";
    const char *dfg     = g_dark_mode ? "#6c7086" : "#71717a";
    const char *aibg    = g_dark_mode ? "#181825" : "#ebebef";
    char css[8192];
    snprintf(css, sizeof(css),
        "window{background-color:%s;color:%s;}"
        "entry{background-color:%s;color:%s;"
        "  border:1px solid %s;border-radius:6px;"
        "  padding:6px 12px;font-family:Monospace;font-size:14px;}"
        "entry:focus{border-color:#cba6f7;}"
        "button{background:%s;color:%s;"
        "  border:1px solid %s;border-radius:6px;"
        "  padding:6px 18px;font-size:13px;}"
        "button:hover{background:%s;border-color:#cba6f7;}"
        ".btn-help{background:%s;color:%s;"
        "  border:1px solid %s;padding:6px 12px;font-size:12px;}"
        ".btn-help:hover{color:#cba6f7;border-color:#cba6f7;}"
        ".btn-model{background:%s;color:#a6adc8;"
        "  border:1px solid %s;border-radius:6px;"
        "  padding:4px 12px;font-size:12px;}"
        ".btn-model:hover{background:%s;color:%s;}"
        ".btn-model-active{background:#2d7d9a;color:#cdd6f4;border-color:#89dceb;}"
        ".title-label{color:#cba6f7;font-size:20px;font-weight:bold;"
        "  font-family:Monospace;}"
        ".status-ok  {color:#a6e3a1;font-size:12px;font-style:italic;}"
        ".status-err {color:#f38ba8;font-size:12px;}"
        ".status-busy{color:#89b4fa;font-size:12px;font-style:italic;}"
        ".dim-label  {color:%s;font-size:12px;}"
        "paned separator{background:#45475a;min-height:4px;}"
        "textview.ai-view,textview.ai-view text{"
        "  background-color:%s;color:%s;"
        "  font-family:Arial,sans-serif;font-size:13px;}",
        bg,fg, ebg,fg,ebd, bbg,fg,bbd,bhov,
        bg,dfg,bbd, bbg,bbd,bhov,fg, dfg, aibg,fg
    );
    gtk_css_provider_load_from_string(g_css, css);
    g_object_set(gtk_settings_get_default(),
                 "gtk-application-prefer-dark-theme", g_dark_mode, NULL);
}

/* ── Helpers ─────────────────────────────────────────────────────────── */
static void set_status(const char *msg, const char *cls) {
    gtk_label_set_text(GTK_LABEL(g_statusbar), msg);
    gtk_widget_remove_css_class(g_statusbar, "status-ok");
    gtk_widget_remove_css_class(g_statusbar, "status-err");
    gtk_widget_remove_css_class(g_statusbar, "status-busy");
    if (cls) gtk_widget_add_css_class(g_statusbar, cls);
}

static void set_busy(gboolean busy) {
    g_busy = busy;
    gtk_widget_set_sensitive(g_btn_fetch, !busy);
    gtk_widget_set_sensitive(g_entry,     !busy);
    if (busy) gtk_spinner_start(GTK_SPINNER(g_spinner));
    else      gtk_spinner_stop(GTK_SPINNER(g_spinner));
}

static void load_html(const char *html) {
    webkit_web_view_load_html(g_webview, html, "about:blank");
}

static void update_model_buttons(void) {
    for (int s = 0; s < 3; s++) {
        gtk_widget_remove_css_class(g_btn_model[s], "btn-model-active");
        if (s+1 == g_current_slot)
            gtk_widget_add_css_class(g_btn_model[s], "btn-model-active");
    }
    gtk_label_set_text(GTK_LABEL(g_label_model),
                       g_model_name[g_current_slot-1]);
}

/* ── AI Analysis ─────────────────────────────────────────────────────── */
static void ai_set_text(const char *text) {
    GtkTextBuffer *b = gtk_text_view_get_buffer(
        GTK_TEXT_VIEW(g_analysis_text));
    gtk_text_buffer_set_text(b, text, -1);
}

static gboolean ai_poll_cb(gpointer ud) {
    (void)ud;
    if (g_ipc_fd_read < 0) { g_ai_poll_timer=0; return G_SOURCE_REMOVE; }
    static char ibuf[65536]; static int ibuf_pos = 0;
    char c;
    while (read(g_ipc_fd_read, &c, 1) == 1) {
        if (c == '\n') {
            ibuf[ibuf_pos] = '\0'; ibuf_pos = 0;
            char *rp = strstr(ibuf, "\"rsp\":");
            if (!rp) continue;
            int rsp = atoi(rp + 6);
            if (rsp == 1) {
                char *tok = strstr(ibuf, "\"token\":\"");
                if (tok) {
                    tok += 9;
                    char token[4096];
                    int tl = json_unescape(tok, token, (int)sizeof(token));
                    /* grow accum dynamically if needed */
                    if (g_ai_accum == NULL || (size_t)(g_ai_accum_len + tl + 1) > g_ai_accum_sz) {
                        size_t nsz = g_ai_accum_sz == 0 ? 65536 : g_ai_accum_sz * 2;
                        while (nsz < (size_t)(g_ai_accum_len + tl + 1)) nsz *= 2;
                        char *nb = realloc(g_ai_accum, nsz);
                        if (nb) { g_ai_accum = nb; g_ai_accum_sz = nsz; }
                    }
                    if (g_ai_accum && (size_t)(g_ai_accum_len + tl + 1) <= g_ai_accum_sz) {
                        memcpy(g_ai_accum+g_ai_accum_len, token, (size_t)tl);
                        g_ai_accum_len += tl;
                        g_ai_accum[g_ai_accum_len] = '\0';
                    }
                    ai_set_text(g_ai_accum);
                }
            } else if (rsp == 2) {
                char full[65536]; full[0] = '\0';
                char *txt = strstr(ibuf, "\"text\":\"");
                if (txt) { txt+=8; json_unescape(txt, full, (int)sizeof(full)); }
                if (full[0]) ai_set_text(full);
                else if (g_ai_accum_len > 0) ai_set_text(g_ai_accum);
                g_ai_accum_len = 0; if(g_ai_accum) g_ai_accum[0]='\0';
                set_status("Analysis done.", "status-ok");
                gtk_widget_set_sensitive(g_btn_analyze, TRUE);
                /* clear mshell context memory for all 3 slots */
                for (int s = 1; s <= 3; s++) {
                    char clrtxt[64], clrjson[256];
                    snprintf(clrtxt, sizeof(clrtxt),
                        "clear_memory_ollama%d", s);
                    char *ep = json_escape(clrtxt);
                    if (ep) {
                        snprintf(clrjson, sizeof(clrjson),
                            "{\"v\":1,\"id\":\"%d\",\"cmd\":1,"
                            "\"code\":\"%s\",\"model\":%d}",
                            g_ipc_seq++, ep, s);
                        ipc_send_raw(clrjson);
                        free(ep);
                        usleep(50000); /* 50ms — дать mshell обработать */
                    }
                }
                g_ai_poll_timer = 0; return G_SOURCE_REMOVE;
            } else if (rsp == 11) {
                char em[256] = "AI error";
                char *err = strstr(ibuf, "\"error\":\"");
                if (err) json_unescape(err+9, em, (int)sizeof(em));
                ai_set_text(em);
                set_status(em, "status-err");
                gtk_widget_set_sensitive(g_btn_analyze, TRUE);
                g_ai_poll_timer = 0; return G_SOURCE_REMOVE;
            }
        } else {
            if (ibuf_pos < (int)sizeof(ibuf)-1) ibuf[ibuf_pos++] = c;
        }
    }
    return G_SOURCE_CONTINUE;
}

static void do_analyze(void) {
    ipc_connect();
    if (g_ipc_fd_write < 0) {
        set_status("mshell not available. Run inside mshell session.",
                   "status-err");
        return;
    }
    if (!g_last_query[0]) {
        set_status("Fetch data first.", "status-err");
        return;
    }
    gtk_widget_set_sensitive(g_btn_analyze, FALSE);
    g_ai_accum_len = 0; if(g_ai_accum) g_ai_accum[0]='\0';
    ai_set_text("Analyzing...");

    /* build lang prefix for system prompt */
    char lang_instr[128];
    snprintf(lang_instr, sizeof(lang_instr),
        " You MUST respond in %s language only.", LANGUAGES[g_lang_idx]);

    /* system prompt — use real newlines */
    char sys_full[1024];
    snprintf(sys_full, sizeof(sys_full),
        "You are a data analyst. Analyze the provided data concisely:\n"
        "- IMPORTANT: cover EVERY symbol/city/country in the data - do not skip any\n"
        "- Summarize by TREND PHASES and key turning points, NOT point-by-point\n"
        "- Notable highs, lows, anomalies with dates\n"
        "- Cross-series comparison: relative performance, divergences\n"
        "- One-sentence conclusion\n"
        "Plain text, no markdown. 3-6 sentences per series maximum.%s",
        lang_instr);
    char *ep = json_escape(sys_full);
    if (ep) {
        char json[4096];
        snprintf(json, sizeof(json),
            "{\"v\":1,\"id\":\"%d\",\"cmd\":1,"
            "\"text\":\"%s\",\"model\":1}",
            g_ipc_seq++, ep);
        ipc_send_raw(json);
        free(ep);
    }

    /* read last_data.json - dynamic buffer, no size limit */
    char *data_json = NULL;
    {
        FILE *jf = fopen(LAST_DATA, "r");
        if (jf) {
            fseek(jf, 0, SEEK_END);
            long fsz = ftell(jf);
            rewind(jf);
            if (fsz > 0 && fsz < 512*1024) {
                data_json = malloc(fsz + 1);
                if (data_json) {
                    int n = (int)fread(data_json, 1, fsz, jf);
                    data_json[n > 0 ? n : 0] = '\0';
                }
            }
            fclose(jf);
        }
        if (!data_json) { data_json = malloc(1); if(data_json) data_json[0]='\0'; }
    }

    /* build prompt - dynamic, full JSON, no truncation */
    char *prompt = NULL;
    {
        int psize = (int)strlen(g_last_query) + (int)strlen(data_json) + 256;
        prompt = malloc(psize);
        if (prompt) {
            if (data_json[0])
                snprintf(prompt, psize,
                    "Query: %s\nData: %s\nAnalyze this data. Respond in %s.",
                    g_last_query, data_json, LANGUAGES[g_lang_idx]);
            else
                snprintf(prompt, psize,
                    "Query: %s\nDescribe what insights a user might expect. Respond in %s.",
                    g_last_query, LANGUAGES[g_lang_idx]);
        }
    }

    ipc_set_provider(g_current_slot);
    ep = json_escape(prompt);
    if (ep) {
        size_t jlen = strlen(ep) + 64;
        char *json = malloc(jlen);
        if (json) {
            snprintf(json, jlen,
                "{\"v\":1,\"id\":\"%d\",\"cmd\":1,"
                "\"code\":\"%s\",\"model\":%d}",
                g_ipc_seq++, ep, g_current_slot);
            ipc_send_raw(json);
            free(json);
        }
        free(ep);
    }
    set_status("AI analyzing...", "status-busy");
    free(data_json);
    free(prompt);
    if (!g_ai_poll_timer)
        g_ai_poll_timer = g_timeout_add(50, ai_poll_cb, NULL);
}

/* ── Worker thread ───────────────────────────────────────────────────── */
static void work_ctx_free(WorkCtx *ctx) {
    free(ctx->query); free(ctx->html); free(ctx->errmsg); free(ctx);
}

static gboolean work_done_cb(gpointer data) {
    WorkCtx *ctx = (WorkCtx*)data;
    if (ctx->errmsg) {
        char *html = NULL;
        (void)asprintf(&html,
            "<!DOCTYPE html><html>"
            "<body style='background:#1e1e2e;color:#f38ba8;"
            "font-family:monospace;padding:40px'>"
            "<h2>Error</h2><pre>%s</pre></body></html>",
            ctx->errmsg);
        if (html) { load_html(html); free(html); }
        set_status(ctx->errmsg, "status-err");
    } else if (ctx->html) {
        load_html(ctx->html);
        char st[256] = "Done.";
        char *t = strstr(ctx->html, "<title>");
        if (t) {
            t += 7; char *e = strstr(t, "</title>");
            if (e) { int l=(int)(e-t); if(l>0&&l<200)
                snprintf(st,sizeof(st),"Done: %.*s",l,t); }
        }
        set_status(st, "status-ok");
        /* trigger AI analysis immediately - last_data.json written synchronously */
        if (g_ai_enabled && g_ipc_fd_write >= 0)
            do_analyze();
    }
    set_busy(FALSE);
    work_ctx_free(ctx);
    return G_SOURCE_REMOVE;
}

static void *worker_thread(void *data) {
    WorkCtx *ctx = (WorkCtx*)data;
    int pin[2], pout[2];
    if (pipe(pin) || pipe(pout)) {
        ctx->errmsg = strdup("pipe() failed");
        g_idle_add(work_done_cb, ctx); return NULL;
    }
    pid_t pid = fork();
    if (pid < 0) {
        ctx->errmsg = strdup("fork() failed");
        g_idle_add(work_done_cb, ctx); return NULL;
    }
    if (pid == 0) {
        dup2(pin[0], STDIN_FILENO);
        dup2(pout[1], STDOUT_FILENO);
        close(pin[0]); close(pin[1]);
        close(pout[0]); close(pout[1]);
        execl("/usr/bin/python3", "python3", DANA_WORKER, NULL);
        _exit(127);
    }
    close(pin[0]); close(pout[1]);
    (void)write(pin[1], ctx->query, strlen(ctx->query));
    (void)write(pin[1], "\n", 1);
    close(pin[1]);
    size_t sz = 65536, pos = 0;
    char *buf = malloc(sz);
    if (!buf) { ctx->errmsg = strdup("OOM"); goto done; }
    ssize_t n;
    while ((n = read(pout[0], buf+pos, sz-pos-1)) > 0) {
        pos += (size_t)n;
        if (pos+1 >= sz) {
            sz *= 2;
            char *nb = realloc(buf, sz);
            if (!nb) { free(buf); buf=NULL; ctx->errmsg=strdup("OOM"); goto done; }
            buf = nb;
        }
    }
    buf[pos] = '\0';
    if (strncmp(buf, "ERROR:", 6) == 0) {
        ctx->errmsg = strdup(buf+7); free(buf);
    } else { ctx->html = buf; }
done:
    close(pout[0]);
    waitpid(pid, NULL, 0);
    g_idle_add(work_done_cb, ctx);
    return NULL;
}

static void do_fetch(void) {
    if (g_busy) return;
    const char *q = gtk_editable_get_text(GTK_EDITABLE(g_entry));
    if (!q || !*q) { set_status("Enter a query.", "status-err"); return; }
    strncpy(g_last_query, q, sizeof(g_last_query)-1);
    /* clear stale analysis */
    ai_set_text("");
    set_busy(TRUE);
    char st[512];
    snprintf(st, sizeof(st), "Fetching: %s ...", q);
    set_status(st, "status-busy");
    WorkCtx *ctx = calloc(1, sizeof(WorkCtx));
    /* prepend units= prefix to query */
    char prefixed[600];
    snprintf(prefixed, sizeof(prefixed), "units=%s %s",
             g_imperial ? "imperial" : "metric", q);
    ctx->query = strdup(prefixed);
    GThread *t = g_thread_new("worker", worker_thread, ctx);
    g_thread_unref(t);
}

/* ── Callbacks ───────────────────────────────────────────────────────── */
static void on_lang_changed(GObject *obj, GParamSpec *ps, gpointer d) {
    (void)ps;(void)d;
    g_lang_idx = (int)gtk_drop_down_get_selected(GTK_DROP_DOWN(obj));
    /* re-analyze immediately with new language if panel visible */
    if (g_ai_enabled && g_last_query[0] && g_ipc_fd_write >= 0)
        do_analyze();
}

static void on_units_switch(GObject *obj, GParamSpec *ps, gpointer d) {
    (void)ps;(void)d;
    g_imperial = gtk_switch_get_active(GTK_SWITCH(obj));
}

static void on_fetch(GtkButton *b, gpointer d)      {(void)b;(void)d; do_fetch();}
static void on_entry_activate(GtkEntry *e, gpointer d){(void)e;(void)d; do_fetch();}
static void on_analyze(GtkButton *b, gpointer d)    {(void)b;(void)d; do_analyze();}

static void on_model_btn(GtkButton *b, gpointer data) {
    (void)b;
    g_current_slot = GPOINTER_TO_INT(data);
    update_model_buttons();
}

static void on_theme_switch(GObject *obj, GParamSpec *ps, gpointer d) {
    (void)ps;(void)d;
    g_dark_mode = gtk_switch_get_active(GTK_SWITCH(obj));
    apply_css();
    /* update webview background */
    GdkRGBA bg = g_dark_mode
        ? (GdkRGBA){0.118, 0.118, 0.180, 1.0}   /* #1e1e2e */
        : (GdkRGBA){0.957, 0.957, 0.961, 1.0};   /* #f4f4f5 */
    webkit_web_view_set_background_color(g_webview, &bg);
    webkit_web_view_evaluate_javascript(g_webview,
        g_dark_mode
        ? "document.body&&(document.body.style.background='#1e1e2e')"
        : "document.body&&(document.body.style.background='#f8f8f8')",
        -1, NULL, NULL, NULL, NULL, NULL);
}

static void on_ai_switch(GObject *obj, GParamSpec *ps, gpointer d) {
    (void)ps;(void)d;
    g_ai_enabled = gtk_switch_get_active(GTK_SWITCH(obj));
    gtk_widget_set_visible(g_analysis_panel, g_ai_enabled);
    if (g_ai_enabled && g_last_query[0] && g_ipc_fd_write >= 0)
        do_analyze();
}

static void on_help(GtkButton *b, gpointer d) {
    (void)b;(void)d;
    extern const char HELP_HTML[];
    load_html(HELP_HTML);
    set_status("Help", "status-ok");
}

static gboolean on_key(GtkEventControllerKey *c, guint kv,
                        guint kc, GdkModifierType st, gpointer d) {
    (void)c;(void)kc;(void)st;(void)d;
    if (kv == GDK_KEY_F1) { on_help(NULL,NULL); return TRUE; }
    return FALSE;
}

/* ── Help HTML ───────────────────────────────────────────────────────── */
const char HELP_HTML[] =
"<!DOCTYPE html><html><head><style>"
"body{background:#1e1e2e;color:#cdd6f4;font-family:'Segoe UI',Arial,sans-serif;"
"     padding:32px;margin:0;line-height:1.7;}"
"h1{color:#cba6f7;font-size:22px;margin-bottom:4px;}"
"h2{color:#89b4fa;font-size:15px;margin:24px 0 8px;"
"   border-bottom:1px solid #313244;padding-bottom:4px;}"
"code{background:#313244;color:#a6e3a1;padding:2px 7px;"
"     border-radius:4px;font-family:monospace;font-size:13px;}"
".cmd{display:flex;gap:16px;margin:6px 0;align-items:baseline;}"
".c{min-width:300px;}"
".d{color:#a6adc8;font-size:13px;}"
".ex{color:#585b70;font-size:12px;margin-top:2px;}"
"hr{border:none;border-top:1px solid #313244;margin:20px 0;}"
".tag{background:#45475a;color:#cba6f7;padding:1px 8px;"
"     border-radius:10px;font-size:11px;margin-left:6px;}"
"</style></head><body>"
"<h1>dana <span style='color:#6c7086;font-size:14px;font-weight:normal'>"
"&mdash; Data Analysis Viewer</span></h1>"
"<p style='color:#6c7086;font-size:13px'>Type a command and press Enter or Fetch.</p>"
"<hr>"
"<h2>&#x1F4C8; Charts &mdash; single</h2>"
"<div class='cmd'><div class='c'><code>stock AAPL 30</code></div>"
"<div><div class='d'>Stock price + volume <span class='tag'>yfinance</span></div>"
"<div class='ex'>stock TSLA 60 &nbsp;&middot;&nbsp; stock NVDA 90</div></div></div>"
"<div class='cmd'><div class='c'><code>crypto bitcoin 30</code></div>"
"<div><div class='d'>Crypto price <span class='tag'>CoinGecko</span></div>"
"<div class='ex'>crypto ethereum 90 &nbsp;&middot;&nbsp; crypto solana 60</div></div></div>"
"<div class='cmd'><div class='c'><code>currency USD EUR 30</code></div>"
"<div><div class='d'>Exchange rate <span class='tag'>yfinance</span></div>"
"<div class='ex'>currency GBP JPY 60 &nbsp;&middot;&nbsp; currency USD RUB 90</div></div></div>"
"<div class='cmd'><div class='c'><code>weather London 14</code></div>"
"<div><div class='d'>Temperature + precipitation <span class='tag'>Open-Meteo</span></div>"
"<div class='ex'>weather Tokyo 7 &nbsp;&middot;&nbsp; weather Moscow 30 &nbsp;&middot;&nbsp; weather &#34;San Francisco,CA&#34; 14 &nbsp;&middot;&nbsp; weather &#34;Springfield,IL&#34;</div></div></div>"
"<div class='cmd'><div class='c'><code>gdp China USA 20</code></div>"
"<div><div class='d'>GDP by year <span class='tag'>World Bank</span></div>"
"<div class='ex'>gdp Germany France Italy 30</div></div></div>"
"<div class='cmd'><div class='c'><code>airquality Paris</code></div>"
"<div><div class='d'>Air quality + pollutants <span class='tag'>Open-Meteo</span></div>"
"<div class='ex'>airquality Beijing &nbsp;&middot;&nbsp; airquality London &nbsp;&middot;&nbsp; airquality &#34;Los Angeles,CA&#34;</div></div></div>"
"<hr>"
"<h2>&#x1F4CA; Charts &mdash; comparison (multiple items)</h2>"
"<div class='cmd'><div class='c'><code>stock AAPL TSLA NVDA 90</code></div>"
"<div class='d'>Normalised to base 100</div></div>"
"<div class='cmd'><div class='c'><code>crypto bitcoin ethereum solana 60</code></div>"
"<div class='d'>Normalised to base 100</div></div>"
"<div class='cmd'><div class='c'><code>weather London Paris Berlin 14</code></div>"
"<div class='d'>Max temperature comparison &mdash; e.g. <code>weather &#34;San Francisco,CA&#34; Moscow &#34;New York,NY&#34; 30</code></div></div>"
"<div class='cmd'><div class='c'><code>currency USD EUR GBP JPY 30</code></div>"
"<div class='d'>Multiple targets vs base</div></div>"
"<hr>"
"<h2>&#x1F5FA; Maps</h2>"
"<div class='cmd'><div class='c'><code>flights Europe</code></div>"
"<div><div class='d'>Live flights <span class='tag'>OpenSky</span></div>"
"<div class='ex'>flights USA &nbsp;&middot;&nbsp; flights Japan &nbsp;&middot;&nbsp;"
" flights Russia &nbsp;&middot;&nbsp; flights world</div></div></div>"
"<div class='cmd'><div class='c'><code>weathermap Europe</code></div>"
"<div><div class='d'>City temperatures <span class='tag'>Open-Meteo</span></div>"
"<div class='ex'>weathermap Japan &nbsp;&middot;&nbsp; weathermap China &nbsp;&middot;&nbsp;"
" weathermap MiddleEast &nbsp;&middot;&nbsp; weathermap Africa &nbsp;&middot;&nbsp;"
" weathermap LatAm &nbsp;&middot;&nbsp; weathermap Australia &nbsp;&middot;&nbsp;"
" weathermap world</div></div></div>"
"<hr>"
"<h2>&#x2328; Shortcuts</h2>"
"<div class='cmd'><div class='c'><code>Enter</code></div><div class='d'>Fetch</div></div>"
"<div class='cmd'><div class='c'><code>F1</code></div><div class='d'>This help</div></div>"
"<hr>"
"<h2>&#x1F4CB; Quick reference</h2>"
"<table style='width:100%;border-collapse:collapse;font-size:11px;color:#6c7086'>"
"<tr style='color:#585b70;border-bottom:1px solid #313244'>"
"  <td style='padding:4px 8px;font-weight:bold;color:#a6adc8'>Command</td>"
"  <td style='padding:4px 8px;font-weight:bold;color:#a6adc8'>Syntax</td>"
"  <td style='padding:4px 8px;font-weight:bold;color:#a6adc8'>Range</td>"
"  <td style='padding:4px 8px;font-weight:bold;color:#a6adc8'>Valid values / examples</td>"
"</tr>"
"<tr style='border-bottom:1px solid #313244'>"
"  <td style='padding:4px 8px'><code>stock</code></td>"
"  <td style='padding:4px 8px'>stock TICKER [days]</td>"
"  <td style='padding:4px 8px'>1–3650 days</td>"
"  <td style='padding:4px 8px'>AAPL TSLA NVDA MSFT AMZN GOOG META NFLX AMD INTC BRK-B SPY QQQ</td>"
"</tr>"
"<tr style='border-bottom:1px solid #313244'>"
"  <td style='padding:4px 8px'><code>crypto</code></td>"
"  <td style='padding:4px 8px'>crypto COIN [days]</td>"
"  <td style='padding:4px 8px'>1–365 days</td>"
"  <td style='padding:4px 8px'>bitcoin ethereum solana dogecoin cardano ripple polkadot avalanche</td>"
"</tr>"
"<tr style='border-bottom:1px solid #313244'>"
"  <td style='padding:4px 8px'><code>currency</code></td>"
"  <td style='padding:4px 8px'>currency BASE TARGET [days]</td>"
"  <td style='padding:4px 8px'>1–3650 days</td>"
"  <td style='padding:4px 8px'>USD EUR GBP JPY CHF CAD AUD CNY RUB INR BRL MXN SEK NOK DKK HKD SGD</td>"
"</tr>"
"<tr style='border-bottom:1px solid #313244'>"
"  <td style='padding:4px 8px'><code>weather</code></td>"
"  <td style='padding:4px 8px'>weather CITY[,State|CC] [days]</td>"
"  <td style='padding:4px 8px'>1–92 days</td>"
"  <td style='padding:4px 8px'>London &nbsp; Tokyo &nbsp; &#34;San Francisco,CA&#34; &nbsp; &#34;Springfield,IL&#34; &nbsp; &#34;Paris,FR&#34; &nbsp; &#34;Paris,TX&#34;</td>"
"</tr>"
"<tr style='border-bottom:1px solid #313244'>"
"  <td style='padding:4px 8px'><code>airquality</code></td>"
"  <td style='padding:4px 8px'>airquality CITY[,State|CC]</td>"
"  <td style='padding:4px 8px'>last 3 days</td>"
"  <td style='padding:4px 8px'>Any city. Qualifier: &#34;Houston,TX&#34; &#34;Lyon,FR&#34;. Shows PM2.5, NO₂, Ozone</td>"
"</tr>"
"<tr style='border-bottom:1px solid #313244'>"
"  <td style='padding:4px 8px'><code>gdp</code></td>"
"  <td style='padding:4px 8px'>gdp COUNTRY... [years]</td>"
"  <td style='padding:4px 8px'>1–60 years</td>"
"  <td style='padding:4px 8px'>USA China Russia Germany France UK Japan India Brazil EU &mdash; or any ISO2 code</td>"
"</tr>"
"<tr style='border-bottom:1px solid #313244'>"
"  <td style='padding:4px 8px'><code>flights</code></td>"
"  <td style='padding:4px 8px'>flights REGION</td>"
"  <td style='padding:4px 8px'>live data</td>"
"  <td style='padding:4px 8px'>Europe USA Asia Japan China Russia MiddleEast Africa LatAm Australia World</td>"
"</tr>"
"<tr>"
"  <td style='padding:4px 8px'><code>weathermap</code></td>"
"  <td style='padding:4px 8px'>weathermap REGION</td>"
"  <td style='padding:4px 8px'>current temp</td>"
"  <td style='padding:4px 8px'>Europe USA Asia Japan China Russia MiddleEast Africa LatAm Australia World</td>"
"</tr>"
"</table>"
"<p style='color:#45475a;font-size:11px;margin-top:14px'>"
"Multiple items: space-separated &mdash; "
"<code>stock AAPL TSLA NVDA 90</code> &nbsp;"
"<code>weather London Paris Berlin 14</code> &nbsp;"
"<code>currency USD EUR GBP JPY 30</code><br>"
"Multi-word names: quotes or underscores &mdash; <code>weather &#34;San Francisco&#34; New_York Moscow 30</code><br>"
"Disambiguation: City,State or City,CC &mdash; "
"<code>weather &#34;Springfield,IL&#34; &#34;Springfield,MO&#34; 14</code> &nbsp;"
"<code>weather &#34;Paris,FR&#34; &#34;Paris,TX&#34; 30</code><br>"
"EU alias for GDP expands to: Germany, France, Italy, Spain, Netherlands, Poland<br>"
"Imperial units: toggle &#xb0;F switch in toolbar (affects weather temperature, precipitation, wind speed)<br>"
"AI Analysis: requires mshell session with MSHELL_IPC_PID set"
"</p>"
"<hr>"
"<p style='color:#45475a;font-size:11px'>"
"Sources: Open-Meteo &middot; CoinGecko &middot; OpenSky Network &middot; "
"World Bank &middot; Yahoo Finance (yfinance) &mdash; all free, no API keys required."
"</p></body></html>";

/* ── Build UI ────────────────────────────────────────────────────────── */
static void build_ui(GtkApplication *app) {
    setenv("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", "1", 1);
    setenv("WEBKIT_DISABLE_DMABUF_RENDERER", "1", 1);

    g_window = gtk_application_window_new(app);
    gtk_window_set_title(GTK_WINDOW(g_window), APP_TITLE);
    gtk_window_set_default_size(GTK_WINDOW(g_window), APP_W, APP_H);

    g_css = gtk_css_provider_new();
    gtk_style_context_add_provider_for_display(
        gdk_display_get_default(),
        GTK_STYLE_PROVIDER(g_css),
        GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
    apply_css();

    GtkEventController *key = gtk_event_controller_key_new();
    g_signal_connect(key, "key-pressed", G_CALLBACK(on_key), NULL);
    gtk_widget_add_controller(g_window, key);

    GtkWidget *vbox = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_window_set_child(GTK_WINDOW(g_window), vbox);

    /* ── Topbar ── */
    GtkWidget *topbar = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    gtk_widget_set_margin_start(topbar, 14);
    gtk_widget_set_margin_end(topbar, 14);
    gtk_widget_set_margin_top(topbar, 10);
    gtk_widget_set_margin_bottom(topbar, 8);
    gtk_box_append(GTK_BOX(vbox), topbar);

    GtkWidget *title = gtk_label_new("dana");
    gtk_widget_add_css_class(title, "title-label");
    gtk_box_append(GTK_BOX(topbar), title);

    g_entry = gtk_entry_new();
    gtk_entry_set_placeholder_text(GTK_ENTRY(g_entry),
        "stock AAPL 30  |  crypto bitcoin  |  weather London 14"
        "  |  flights Europe  |  F1 help");
    gtk_widget_set_hexpand(g_entry, TRUE);
    gtk_box_append(GTK_BOX(topbar), g_entry);
    g_signal_connect(g_entry, "activate", G_CALLBACK(on_entry_activate), NULL);

    g_btn_fetch = gtk_button_new_with_label("Fetch \xe2\x86\x97");
    gtk_box_append(GTK_BOX(topbar), g_btn_fetch);
    g_signal_connect(g_btn_fetch, "clicked", G_CALLBACK(on_fetch), NULL);

    g_btn_help = gtk_button_new_with_label("? Help");
    gtk_widget_add_css_class(g_btn_help, "btn-help");
    gtk_box_append(GTK_BOX(topbar), g_btn_help);
    g_signal_connect(g_btn_help, "clicked", G_CALLBACK(on_help), NULL);

    GtkWidget *thlbl = gtk_label_new("Dark");
    gtk_widget_add_css_class(thlbl, "dim-label");
    gtk_box_append(GTK_BOX(topbar), thlbl);
    g_theme_switch = gtk_switch_new();
    gtk_switch_set_active(GTK_SWITCH(g_theme_switch), TRUE);
    gtk_widget_set_valign(g_theme_switch, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(topbar), g_theme_switch);
    g_signal_connect(g_theme_switch, "notify::active",
                     G_CALLBACK(on_theme_switch), NULL);

    GtkWidget *ulbl = gtk_label_new("Imperial");
    gtk_widget_add_css_class(ulbl, "dim-label");
    gtk_box_append(GTK_BOX(topbar), ulbl);
    g_units_switch = gtk_switch_new();
    gtk_switch_set_active(GTK_SWITCH(g_units_switch), FALSE);
    gtk_widget_set_valign(g_units_switch, GTK_ALIGN_CENTER);
    gtk_widget_set_tooltip_text(g_units_switch,
        "OFF = Metric (°C/km), ON = Imperial (°F/miles)");
    gtk_box_append(GTK_BOX(topbar), g_units_switch);
    g_signal_connect(g_units_switch, "notify::active",
        G_CALLBACK(on_units_switch), NULL);

    g_spinner = gtk_spinner_new();
    gtk_widget_set_size_request(g_spinner, 20, 20);
    gtk_box_append(GTK_BOX(topbar), g_spinner);

    /* ── Model row ── */
    GtkWidget *mrow = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    gtk_widget_set_margin_start(mrow, 14);
    gtk_widget_set_margin_end(mrow, 14);
    gtk_widget_set_margin_bottom(mrow, 6);
    gtk_box_append(GTK_BOX(vbox), mrow);

    GtkWidget *mlbl = gtk_label_new("Model:");
    gtk_widget_add_css_class(mlbl, "dim-label");
    gtk_box_append(GTK_BOX(mrow), mlbl);

    for (int s = 0; s < 3; s++) {
        g_btn_model[s] = gtk_button_new_with_label(g_model_short[s]);
        gtk_widget_add_css_class(g_btn_model[s], "btn-model");
        g_signal_connect(g_btn_model[s], "clicked",
                         G_CALLBACK(on_model_btn), GINT_TO_POINTER(s+1));
        gtk_box_append(GTK_BOX(mrow), g_btn_model[s]);
    }

    g_label_model = gtk_label_new("");
    gtk_widget_set_halign(g_label_model, GTK_ALIGN_START);
    gtk_widget_set_hexpand(g_label_model, TRUE);
    gtk_label_set_ellipsize(GTK_LABEL(g_label_model), PANGO_ELLIPSIZE_END);
    gtk_widget_add_css_class(g_label_model, "status-ok");
    gtk_box_append(GTK_BOX(mrow), g_label_model);
    update_model_buttons();

    /* AI Analysis switch */
    gtk_box_append(GTK_BOX(mrow), gtk_separator_new(GTK_ORIENTATION_VERTICAL));
    GtkWidget *ailbl = gtk_label_new("AI Analysis");
    gtk_widget_add_css_class(ailbl, "dim-label");
    gtk_box_append(GTK_BOX(mrow), ailbl);
    g_ai_switch = gtk_switch_new();
    gtk_switch_set_active(GTK_SWITCH(g_ai_switch), FALSE);
    gtk_widget_set_valign(g_ai_switch, GTK_ALIGN_CENTER);
    ipc_connect();
    if (g_ipc_fd_write < 0) {
        gtk_widget_set_sensitive(g_ai_switch, FALSE);
        gtk_widget_set_tooltip_text(g_ai_switch,
            "Requires mshell (MSHELL_IPC_PID)");
    }
    gtk_box_append(GTK_BOX(mrow), g_ai_switch);
    g_signal_connect(g_ai_switch, "notify::active",
                     G_CALLBACK(on_ai_switch), NULL);

    /* ── Status ── */
    g_statusbar = gtk_label_new("Ready. Press F1 for help.");
    gtk_widget_add_css_class(g_statusbar, "status-ok");
    gtk_widget_set_halign(g_statusbar, GTK_ALIGN_START);
    gtk_widget_set_margin_start(g_statusbar, 14);
    gtk_widget_set_margin_bottom(g_statusbar, 4);
    gtk_label_set_ellipsize(GTK_LABEL(g_statusbar), PANGO_ELLIPSIZE_END);
    gtk_widget_set_hexpand(g_statusbar, TRUE);
    gtk_box_append(GTK_BOX(vbox), g_statusbar);

    /* ── WebView ── */
    g_webview = WEBKIT_WEB_VIEW(webkit_web_view_new());
    gtk_widget_set_vexpand(GTK_WIDGET(g_webview), TRUE);
    gtk_widget_set_hexpand(GTK_WIDGET(g_webview), TRUE);
    WebKitSettings *ws = webkit_web_view_get_settings(g_webview);
    webkit_settings_set_enable_javascript(ws, TRUE);
    /* dark background to prevent white flash on resize */
    GdkRGBA bg_color = {0.118, 0.118, 0.180, 1.0}; /* #1e1e2e */
    webkit_web_view_set_background_color(g_webview, &bg_color);
    /* ── Paned: webview / analysis panel ── */
    GtkWidget *paned = gtk_paned_new(GTK_ORIENTATION_VERTICAL);
    gtk_widget_set_vexpand(paned, TRUE);
    gtk_box_append(GTK_BOX(vbox), paned);

    /* top pane: webview */
    gtk_paned_set_start_child(GTK_PANED(paned), GTK_WIDGET(g_webview));
    gtk_paned_set_resize_start_child(GTK_PANED(paned), TRUE);
    gtk_paned_set_shrink_start_child(GTK_PANED(paned), FALSE);

    /* bottom pane: analysis panel */
    g_analysis_panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_paned_set_end_child(GTK_PANED(paned), g_analysis_panel);
    gtk_paned_set_resize_end_child(GTK_PANED(paned), FALSE);
    gtk_paned_set_shrink_end_child(GTK_PANED(paned), TRUE);
    /* hide bottom pane initially — show when AI switch ON */
    gtk_widget_set_visible(g_analysis_panel, FALSE);

    GtkWidget *aihdr = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    gtk_widget_set_margin_start(aihdr, 14);
    gtk_widget_set_margin_end(aihdr, 14);
    gtk_widget_set_margin_top(aihdr, 6);
    gtk_widget_set_margin_bottom(aihdr, 4);
    gtk_box_append(GTK_BOX(g_analysis_panel), aihdr);

    GtkWidget *aititle = gtk_label_new("\xf0\x9f\xa4\x96 AI Analysis");
    gtk_widget_add_css_class(aititle, "status-ok");
    gtk_box_append(GTK_BOX(aihdr), aititle);

    g_btn_analyze = gtk_button_new_with_label("\xe2\x86\xbb Re-analyze");
    gtk_widget_add_css_class(g_btn_analyze, "btn-help");
    gtk_box_append(GTK_BOX(aihdr), g_btn_analyze);
    g_signal_connect(g_btn_analyze, "clicked", G_CALLBACK(on_analyze), NULL);

    /* Language dropdown */
    GtkWidget *lang_lbl = gtk_label_new("Lang:");
    gtk_widget_add_css_class(lang_lbl, "dim-label");
    gtk_box_append(GTK_BOX(aihdr), lang_lbl);
    GtkStringList *lang_list = gtk_string_list_new(NULL);
    for (int i = 0; i < N_LANGS; i++)
        gtk_string_list_append(lang_list, LANGUAGES[i]);
    g_lang_dd = gtk_drop_down_new(G_LIST_MODEL(lang_list), NULL);
    gtk_drop_down_set_selected(GTK_DROP_DOWN(g_lang_dd), 0);
    gtk_box_append(GTK_BOX(aihdr), g_lang_dd);
    g_signal_connect(g_lang_dd, "notify::selected",
        G_CALLBACK(on_lang_changed), NULL);

    /* Spacer */
    GtkWidget *ai_sp = gtk_label_new("");
    gtk_widget_set_hexpand(ai_sp, TRUE);
    gtk_box_append(GTK_BOX(aihdr), ai_sp);

    GtkWidget *aiscroll = gtk_scrolled_window_new();
    gtk_widget_set_vexpand(aiscroll, TRUE);
    gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(aiscroll),
        GTK_POLICY_AUTOMATIC, GTK_POLICY_AUTOMATIC);
    gtk_box_append(GTK_BOX(g_analysis_panel), aiscroll);

    g_analysis_text = gtk_text_view_new();
    gtk_text_view_set_editable(GTK_TEXT_VIEW(g_analysis_text), FALSE);
    gtk_text_view_set_wrap_mode(GTK_TEXT_VIEW(g_analysis_text),
                                GTK_WRAP_WORD_CHAR);
    gtk_text_view_set_left_margin(GTK_TEXT_VIEW(g_analysis_text), 14);
    gtk_text_view_set_right_margin(GTK_TEXT_VIEW(g_analysis_text), 14);
    gtk_text_view_set_top_margin(GTK_TEXT_VIEW(g_analysis_text), 8);
    gtk_text_view_set_bottom_margin(GTK_TEXT_VIEW(g_analysis_text), 8);
    gtk_widget_add_css_class(g_analysis_text, "ai-view");
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(aiscroll),
                                   g_analysis_text);

    load_html(HELP_HTML);
    gtk_window_present(GTK_WINDOW(g_window));
}

static void on_activate(GtkApplication *app, gpointer d) {
    (void)d;
    /* remove stale last_data.json from previous session */
    remove(LAST_DATA);
    load_model_names();
    build_ui(app);
}

int main(int argc, char *argv[]) {
    int devnull = open("/dev/null", O_WRONLY);
    if (devnull >= 0) { dup2(devnull, STDERR_FILENO); close(devnull); }

#ifndef DANA_APP_ID
#define DANA_APP_ID "art2dec.dana"
#endif
    GtkApplication *app = gtk_application_new(
        DANA_APP_ID, G_APPLICATION_DEFAULT_FLAGS);
    g_signal_connect(app, "activate", G_CALLBACK(on_activate), NULL);
    int rc = g_application_run(G_APPLICATION(app), argc, argv);
    g_object_unref(app);
    return rc;
}
