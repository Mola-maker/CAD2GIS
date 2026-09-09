package org.cad2gis.mobile;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** One HTTPS origin is the entire navigation and credential boundary. */
public final class ServerAddress {
    private final String origin;

    private ServerAddress(String origin) { this.origin = origin; }

    public static ServerAddress parse(String input) {
        if (input == null || input.trim().isEmpty()) {
            throw new IllegalArgumentException("请输入 CAD2GIS 服务器的 HTTPS 地址");
        }
        String text = input.trim();
        if (!text.contains("://")) text = "https://" + text;
        try {
            URI uri = new URI(text);
            String path = uri.getRawPath();
            if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null
                    || uri.getRawUserInfo() != null || uri.getRawQuery() != null
                    || uri.getRawFragment() != null || uri.getPort() == 0
                    || uri.getPort() > 65535 || (path != null && !path.isEmpty() && !"/".equals(path))) {
                throw new IllegalArgumentException("请输入 HTTPS 服务根地址，例如 https://cad.example.com");
            }
            String host = uri.getHost().toLowerCase(Locale.ROOT);
            int port = uri.getPort();
            return new ServerAddress("https://" + host + (port == -1 || port == 443 ? "" : ":" + port));
        } catch (URISyntaxException error) {
            throw new IllegalArgumentException("服务器地址格式不正确", error);
        }
    }

    public String origin() { return origin; }
    public String home() { return origin + "/_app/"; }

    public boolean permits(String target) {
        try {
            URI uri = new URI(target);
            if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null
                    || uri.getRawUserInfo() != null) return false;
            String host = uri.getHost().toLowerCase(Locale.ROOT);
            int port = uri.getPort();
            String candidate = "https://" + host + (port == -1 || port == 443 ? "" : ":" + port);
            return origin.equals(candidate);
        } catch (URISyntaxException | NullPointerException error) {
            return false;
        }
    }
}
