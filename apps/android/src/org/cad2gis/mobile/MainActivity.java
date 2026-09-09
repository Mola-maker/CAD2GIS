package org.cad2gis.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageInfo;
import android.graphics.Color;
import android.graphics.Bitmap;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.os.Build;
import android.text.InputType;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.SslErrorHandler;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebStorage;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** A server client: all CAD/GIS operations remain in the canonical backend. */
public final class MainActivity extends Activity {
    private static final int PICK_UPLOAD = 1;
    private static final int SAVE_DOWNLOAD = 2;
    private WebView web;
    private TextView status;
    private ProgressBar progress;
    private volatile ServerAddress server;
    private ValueCallback<Uri[]> uploadCallback;
    private String pendingDownload;
    private boolean pageFailed;
    private boolean resetHistoryAfterNavigation;
    private final ExecutorService downloads = Executors.newSingleThreadExecutor();

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        if (!supportedWebView()) {
            new AlertDialog.Builder(this).setTitle("请更新网页组件")
                    .setMessage("CAD2GIS 需要 Android System WebView 83 或更新版本。请在系统设置或应用商店更新 Android System WebView（部分设备由 Chrome 提供），然后重新打开 CAD2GIS。")
                    .setPositiveButton("关闭", (dialog, which) -> finish()).setCancelable(false).show();
            return;
        }
        getWindow().setStatusBarColor(Color.rgb(11, 107, 97));
        getWindow().setNavigationBarColor(Color.WHITE);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(0, 0, 0, 0);
        layout.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(
                        WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setPadding(dp(8), dp(4), dp(8), dp(4));
        toolbar.setGravity(android.view.Gravity.CENTER_VERTICAL);
        Button home = new Button(this);
        home.setText("CAD2GIS");
        home.setContentDescription("返回服务器首页");
        home.setOnClickListener(v -> { if (server != null) web.loadUrl(server.home()); });
        toolbar.addView(home);
        status = new TextView(this);
        status.setText("连接 CAD2GIS 服务器");
        status.setTextSize(12);
        status.setMaxLines(2);
        toolbar.addView(status, new LinearLayout.LayoutParams(0, -2, 1));
        Button settings = new Button(this);
        settings.setText("服务器");
        settings.setOnClickListener(v -> chooseServer());
        toolbar.addView(settings);
        layout.addView(toolbar, new LinearLayout.LayoutParams(-1, -2));
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        layout.addView(progress, new LinearLayout.LayoutParams(-1, dp(3)));
        web = new WebView(this);
        layout.addView(web, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(layout);
        configureWebView();
        String remembered = getPreferences(MODE_PRIVATE).getString("server", "");
        if (remembered.isEmpty()) chooseServer();
        else {
            try {
                server = ServerAddress.parse(remembered);
                String page = saved == null ? null : saved.getString("page");
                web.loadUrl(server.permits(page) ? page : server.home());
            } catch (IllegalArgumentException error) { chooseServer(); }
        }
    }

    private void configureWebView() {
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setGeolocationEnabled(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);
        settings.setSafeBrowsingEnabled(true);
        settings.setSaveFormData(false);
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageStarted(WebView view, String url, Bitmap favicon) {
                if (view != web) return;
                pageFailed = false;
                status.setText("正在连接…");
            }

            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (server != null && server.permits(request.getUrl().toString())) return false;
                message("已阻止离开当前 CAD2GIS 服务的链接");
                return true;
            }

            @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                String scheme = request.getUrl().getScheme();
                boolean safeResource = "https".equals(scheme) || "data".equals(scheme) || "blob".equals(scheme);
                if (!safeResource || (request.isForMainFrame()
                        && (server == null || !server.permits(request.getUrl().toString())))) {
                    return new WebResourceResponse("text/plain", "utf-8", 403, "Blocked", null,
                            new ByteArrayInputStream(new byte[0]));
                }
                return null;
            }

            @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel();
                if (view != web) return;
                pageFailed = true;
                web.setVisibility(View.INVISIBLE);
                status.setText("证书验证失败");
                message("无法验证服务器 HTTPS 证书，请联系服务器管理员");
            }

            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (view == web && request.isForMainFrame()) {
                    pageFailed = true;
                    web.setVisibility(View.INVISIBLE);
                    status.setText("连接失败，请检查地址或网络后重试");
                }
            }

            @Override public void onPageFinished(WebView view, String url) {
                if (view != web) return;
                if (!pageFailed && server != null && server.permits(url)) {
                    web.setVisibility(View.VISIBLE);
                    status.setText(server.origin());
                    if (resetHistoryAfterNavigation) {
                        web.clearHistory();
                        resetHistoryAfterNavigation = false;
                    }
                }
                CookieManager.getInstance().flush();
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView view, int value) {
                progress.setProgress(value);
                progress.setVisibility(value == 100 ? View.GONE : View.VISIBLE);
            }

            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                    FileChooserParams params) {
                if (server == null || !server.permits(view.getUrl())) return false;
                if (uploadCallback != null) uploadCallback.onReceiveValue(null);
                uploadCallback = callback;
                Intent pick = new Intent(Intent.ACTION_GET_CONTENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                pick.setType("*/*");
                pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                startActivityForResult(pick, PICK_UPLOAD);
                return true;
            }
        });
        web.setDownloadListener((url, agent, disposition, mime, length) -> {
            if (server == null || !server.permits(url)) { message("只能下载当前服务中的文件"); return; }
            pendingDownload = url;
            Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT);
            save.addCategory(Intent.CATEGORY_OPENABLE);
            save.setType(mime == null || mime.isEmpty() ? "application/octet-stream" : mime);
            // A generic server MIME must not replace GIS extensions such as .gpkg with .bin.
            String name = URLUtil.guessFileName(url, disposition, null).replaceAll("[\\\\/:*?\"<>|]", "_");
            save.putExtra(Intent.EXTRA_TITLE, name);
            startActivityForResult(save, SAVE_DOWNLOAD);
        });
    }

    private void chooseServer() {
        EditText address = new EditText(this);
        address.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        address.setSingleLine(true);
        address.setHint("https://cad.example.com");
        address.setText(server == null ? "" : server.origin());
        int padding = dp(20);
        LinearLayout content = new LinearLayout(this);
        content.setPadding(padding, padding, padding, 0);
        content.setOrientation(LinearLayout.VERTICAL);
        TextView description = new TextView(this);
        description.setText("连接已部署的 CAD2GIS 服务。转换、OSM 匹配、SVG 提取和人工校正在服务器执行。");
        content.addView(description);
        content.addView(address);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("连接 CAD2GIS")
                .setView(content).setPositiveButton("连接", null)
                .setNegativeButton("取消", (d, which) -> {}).create();
        dialog.setOnShowListener(ignored -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            try {
                ServerAddress next = ServerAddress.parse(address.getText().toString());
                boolean changed = server == null || !next.origin().equals(server.origin());
                web.stopLoading();
                pendingDownload = null;
                if (uploadCallback != null) { uploadCallback.onReceiveValue(null); uploadCallback = null; }
                server = next;
                getPreferences(MODE_PRIVATE).edit().putString("server", next.origin()).apply();
                dialog.dismiss();
                status.setText("正在连接 " + next.origin());
                if (changed) {
                    resetHistoryAfterNavigation = true;
                    web.clearCache(true);
                    LinearLayout parent = (LinearLayout) web.getParent();
                    android.view.ViewGroup.LayoutParams params = web.getLayoutParams();
                    parent.removeView(web);
                    web.destroy();
                    web = new WebView(this);
                    parent.addView(web, params);
                    configureWebView();
                    WebStorage.getInstance().deleteAllData();
                    CookieManager.getInstance().removeAllCookies(done -> {
                        CookieManager.getInstance().flush();
                        if (server == next) web.loadUrl(next.home());
                    });
                } else {
                    web.setVisibility(View.VISIBLE);
                    web.loadUrl(next.home());
                }
            } catch (IllegalArgumentException error) { address.setError(error.getMessage()); }
        }));
        dialog.show();
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == PICK_UPLOAD && uploadCallback != null) {
            Uri[] selected = null;
            if (result == RESULT_OK && data != null) {
                if (data.getClipData() != null) {
                    selected = new Uri[data.getClipData().getItemCount()];
                    for (int i = 0; i < selected.length; i++) selected[i] = data.getClipData().getItemAt(i).getUri();
                } else if (data.getData() != null) selected = new Uri[]{data.getData()};
            }
            if (selected != null) {
                for (Uri uri : selected) {
                    if (uri == null || !"content".equals(uri.getScheme())) {
                        selected = null;
                        message("请选择系统文件管理器提供的文档");
                        break;
                    }
                }
            }
            uploadCallback.onReceiveValue(selected);
            uploadCallback = null;
        } else if (request == SAVE_DOWNLOAD) {
            String url = pendingDownload;
            pendingDownload = null;
            if (result == RESULT_OK && data != null && data.getData() != null && url != null
                    && server != null && server.permits(url)) download(url, data.getData(), server);
        }
    }

    private void download(String source, Uri destination, ServerAddress allowedServer) {
        String cookies = CookieManager.getInstance().getCookie(source);
        message("正在保存文件…");
        downloads.execute(() -> {
            HttpURLConnection connection = null;
            try {
                String current = source;
                for (int redirects = 0; redirects <= 4; redirects++) {
                    if (!allowedServer.permits(current)) throw new java.io.IOException("下载跳转离开当前服务器");
                    connection = (HttpURLConnection) new URL(current).openConnection();
                    connection.setInstanceFollowRedirects(false);
                    connection.setConnectTimeout(15000);
                    connection.setReadTimeout(60000);
                    if (cookies != null) connection.setRequestProperty("Cookie", cookies);
                    int code = connection.getResponseCode();
                    if (code >= 300 && code < 400) {
                        String location = connection.getHeaderField("Location");
                        if (location == null) throw new java.io.IOException("下载地址缺失");
                        current = new URL(new URL(current), location).toString();
                        connection.disconnect();
                        connection = null;
                        continue;
                    }
                    if (code != 200) throw new java.io.IOException("服务器返回 " + code);
                    try (InputStream input = connection.getInputStream();
                            OutputStream output = getContentResolver().openOutputStream(destination, "wt")) {
                        if (output == null) throw new java.io.IOException("无法写入选择的位置");
                        byte[] buffer = new byte[65536];
                        int count;
                        while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                    }
                    message("文件已保存");
                    return;
                }
                throw new java.io.IOException("下载跳转次数过多");
            } catch (Exception error) { message("保存失败：" + error.getMessage()); }
            finally { if (connection != null) connection.disconnect(); }
        });
    }

    @Override public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        if (web != null) out.putString("page", web.getUrl());
        super.onSaveInstanceState(out);
    }

    @Override protected void onDestroy() {
        if (uploadCallback != null) uploadCallback.onReceiveValue(null);
        downloads.shutdown();
        if (web != null) { web.stopLoading(); web.destroy(); }
        super.onDestroy();
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private void message(String text) { runOnUiThread(() -> Toast.makeText(this, text, Toast.LENGTH_LONG).show()); }

    private boolean supportedWebView() {
        try {
            PackageInfo provider = WebView.getCurrentWebViewPackage();
            if (provider == null || provider.versionName == null) return false;
            // Vendor provider package versions may differ from the Chromium engine version.
            java.util.regex.Matcher engine = java.util.regex.Pattern.compile("(?:Chrome|Chromium)/(\\d+)")
                    .matcher(WebSettings.getDefaultUserAgent(this));
            String major = engine.find() ? engine.group(1) : provider.versionName.split("\\.")[0];
            return Integer.parseInt(major) >= 83;
        } catch (RuntimeException error) { return false; }
    }
}
