
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef enum {CP_UNDEFINED,CP_ANSI_932,CP_ANSI_950,CP_ANSI_949,CP_ANSI_1361,CP_ANSI_936} Dwg_Codepage;
#define LOG_ERROR(...) fprintf(stderr,__VA_ARGS__)
#define memBEGINc(s,t) (memcmp((s),(t),sizeof(t)-1)==0)
static uint32_t dwg_codepage_uwc(Dwg_Codepage cp,uint16_t x){ (void)cp; abort(); return x; }
static char *
cquote (char *restrict dest, const size_t len, const char *restrict src)
{
  char c;
  char *d = dest;
  const char *dend = dest + len;
  const char *send = src + strlen (src);
  char *s = (char *)src;
  while ((s < send) && (c = *s++) && dest < dend)
    {
      if (c == '\n' && dest + 1 < dend)
        {
          *dest++ = '^';
          *dest++ = 'J';
        }
      else if (c == '\r' && dest + 1 < dend)
        {
          *dest++ = '^';
          *dest++ = 'M';
        }
      // Convert Asian MIF \M+nxxxx to \U+xxxx
      // 1: 932 ShiftJIS
      // 2: 950 BIG5
      // 3: 949 Wansung
      // 4: 1361 JOHAB
      // 5: 936 Simplified Chinese
      // See
      // https://docs.intellicad.org/files/oda/2021_11/oda_drawings_docs/frames.html?frmname=topic&frmfile=FontHandling.html
      else if (c == '\\' && dest + 7 < dend && s + 6 < send
               && memBEGINc (s, "M+") && s[3] >= '1' && s[3] <= '5')
        {
          const Dwg_Codepage mif_tbl[]
              = { CP_UNDEFINED, CP_ANSI_932,  CP_ANSI_950,
                  CP_ANSI_949,  CP_ANSI_1361, CP_ANSI_936 };
          int i;
          uint16_t x;
          uint32_t uc;
          Dwg_Codepage cp;
          sscanf (s, "M+%d", &i);
          if (i >= 1 && i <= 5)
            {
              cp = mif_tbl[i];
              sscanf (&s[3], "%4X", &uc);
              x = uc & 0xFFFF;
              uc = dwg_codepage_uwc (cp, x);
              snprintf (dest, dend - dest, "\\U+%04X", uc);
              dest += 7;
              s += 7;
            }
          else
            {
              LOG_ERROR ("Invalid MIF index in %s", s - 1);
              s += 3;
            }
        }
      else
        *dest++ = c;
    }
  d[len - 1] = '\0'; // add final delim, skipped above
  return d;
}
static char *
cquote_fixed (char *restrict dest, const size_t len, const char *restrict src)
{
  char c;
  char *d = dest;
  const char *dend = dest + len;
  const char *send = src + strlen (src);
  char *s = (char *)src;
  while ((s < send) && (c = *s++) && dest < dend)
    {
      if (c == '\n' && dest + 1 < dend)
        {
          *dest++ = '^';
          *dest++ = 'J';
        }
      else if (c == '\r' && dest + 1 < dend)
        {
          *dest++ = '^';
          *dest++ = 'M';
        }
      // Convert Asian MIF \M+nxxxx to \U+xxxx
      // 1: 932 ShiftJIS
      // 2: 950 BIG5
      // 3: 949 Wansung
      // 4: 1361 JOHAB
      // 5: 936 Simplified Chinese
      // See
      // https://docs.intellicad.org/files/oda/2021_11/oda_drawings_docs/frames.html?frmname=topic&frmfile=FontHandling.html
      else if (c == '\\' && dest + 7 < dend && s + 6 < send
               && memBEGINc (s, "M+") && s[3] >= '1' && s[3] <= '5')
        {
          const Dwg_Codepage mif_tbl[]
              = { CP_UNDEFINED, CP_ANSI_932,  CP_ANSI_950,
                  CP_ANSI_949,  CP_ANSI_1361, CP_ANSI_936 };
          int i;
          uint16_t x;
          uint32_t uc;
          Dwg_Codepage cp;
          sscanf (s, "M+%d", &i);
          if (i >= 1 && i <= 5)
            {
              cp = mif_tbl[i];
              sscanf (&s[3], "%4X", &uc);
              x = uc & 0xFFFF;
              uc = dwg_codepage_uwc (cp, x);
              snprintf (dest, dend - dest, "\\U+%04X", uc);
              dest += 7;
              s += 7;
            }
          else
            {
              LOG_ERROR ("Invalid MIF index in %s", s - 1);
              s += 3;
            }
        }
      else
        *dest++ = c;
    }
  if (dest < dend)
    *dest = '\0';
  else
    d[len - 1] = '\0';
  return d;
}

int main(void) {
 const char *input = "MTEXT\nOK";
 const char *expected = "MTEXT^JOK";
 size_t size=2*strlen(input)+1;
 char *old=malloc(size), *fixed=malloc(size);
 memset(old,'X',size); memset(fixed,'X',size);
 cquote(old,size,input); cquote_fixed(fixed,size,input);
 printf("{\"input\":\"MTEXT\\\\nOK\",\"allocation\":%zu,\"expected\":\"%s\",\"old\":\"%s\",\"fixed\":\"%s\",\"old_length\":%zu,\"fixed_length\":%zu}\n",
 size,expected,old,fixed,strlen(old),strlen(fixed));
 int passed = strcmp(old,expected)!=0 && strcmp(fixed,expected)==0;

 free(old);free(fixed);
 const char *cases[] = {"", "A", "\n", "\r", "\n\r\n", "abc\nxyz", "last\r\n", "\xC2\xB0\n"};
 const char *expects[] = {"", "A", "^J", "^M", "^J^M^J", "abc^Jxyz", "last^M^J", "\xC2\xB0^J"};
 for (size_t i=0;i<sizeof(cases)/sizeof(cases[0]);i++) {
   size_t n=2*strlen(cases[i])+1;
   char *buf=malloc(n);
   memset(buf,'Q',n);
   cquote_fixed(buf,n,cases[i]);
   if (strcmp(buf,expects[i])) passed=0;
   free(buf);
 }
 fprintf(stderr,"fixed termination cases: 8; passed: %d\n",passed);
 return passed ? 0 : 1;
}
