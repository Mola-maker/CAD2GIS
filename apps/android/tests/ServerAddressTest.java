import org.cad2gis.mobile.ServerAddress;

public final class ServerAddressTest {
    private static int assertions;
    private static void check(boolean result) {
        assertions++;
        if (!result) throw new AssertionError("Assertion " + assertions + " failed");
    }
    private static void reject(String value) {
        try { ServerAddress.parse(value); throw new AssertionError("Accepted: " + value); }
        catch (IllegalArgumentException expected) { assertions++; }
    }
    public static void main(String[] args) {
        ServerAddress a = ServerAddress.parse("  CAD.EXAMPLE.COM/  ");
        check(a.origin().equals("https://cad.example.com"));
        check(a.home().equals("https://cad.example.com/_app/"));
        check(a.permits("https://CAD.example.com:443/_app/login?next=%2F"));
        check(!a.permits("http://cad.example.com/_app/"));
        check(!a.permits("https://cad.example.com.attacker.example/"));
        check(!a.permits("https://attacker@cad.example.com/"));
        check(!a.permits("https://cad.example.com:444/"));
        check(!a.permits("file:///data/data/app/private"));
        check(!a.permits("content://org.example/secret"));
        check(!a.permits("javascript:alert(1)"));
        check(!a.permits(null));
        check(ServerAddress.parse("https://[::1]:8443").permits("https://[::1]:8443/_app/"));
        check(ServerAddress.parse("https://cad.example.com:8443").permits("https://cad.example.com:8443/"));
        for (String value : new String[]{"http://cad.example.com", "https://user:secret@cad.example.com",
                "https://cad.example.com/path", "https://cad.example.com?token=secret", "https://cad.example.com#x",
                "https://cad.example.com:0", "https://cad.example.com:65536", "https://cad.example.com\\evil",
                "https://cad.example.com\n.evil", "https://", "", "javascript:alert(1)", "//evil.example", null}) {
            reject(value);
        }
        System.out.println(assertions + " server address boundary assertions passed");
    }
}
