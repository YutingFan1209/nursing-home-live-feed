// CSV and Excel downloads of the record table. Pure functions, no DOM, so
// tests/test_csv_export.py can run them under Node and check the bytes.
//
// CSV: UTF-8 with a byte-order mark. Without it Excel for Mac reads the file
// as Mac Roman and an em dash (e2 80 94) shows as "‚Äî" (2026-10-28). Text
// starting with = + - @ tab or CR gets a leading ' so a spreadsheet shows it
// instead of running it as a formula. Values stay otherwise raw: a CCN like
// 015372 loses its zero if Excel opens the CSV, which is what the .xlsx is for.
//
// XLSX: every string is an inline string cell (text, never a formula, so
// leading zeros and long numbers survive), JS numbers are numbers, and
// YYYY-MM-DD values in the date columns are real dates.

const BOM = "﻿";
const FORMULA_START = /^[=+\-@\t\r]/;

export const neutralize = v => typeof v === "string" && FORMULA_START.test(v) ? `'${v}` : v;

const csvCell = v => {
  if (v == null) return "";
  const s = String(neutralize(v));
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export const toCsv = rows => BOM + rows.map(r => r.map(csvCell).join(",")).join("\r\n") + "\r\n";

// --- xlsx ---

const XML_BAD = /[\x00-\x08\x0B\x0C\x0E-\x1F￾￿]/g;
const xml = s => String(s).replace(XML_BAD, "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const colName = i => { let s = ""; for (i += 1; i > 0; i = Math.floor((i - 1) / 26)) s = String.fromCharCode(65 + (i - 1) % 26) + s; return s; };
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const EXCEL_MAX_CELL = 32767;

function xlsxCell(v, ref, isDate) {
  if (v == null || v === "") return "";
  if (typeof v === "number" && Number.isFinite(v)) return `<c r="${ref}"><v>${v}</v></c>`;
  const s = String(v);
  const m = isDate && ISO_DATE.exec(s);
  if (m) {
    const serial = (Date.UTC(+m[1], +m[2] - 1, +m[3]) - Date.UTC(1899, 11, 30)) / 86400000;
    return `<c r="${ref}" s="1"><v>${serial}</v></c>`;
  }
  return `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${xml(s.slice(0, EXCEL_MAX_CELL))}</t></is></c>`;
}

function sheetXml(rows, dateColumns) {
  const dates = new Set((rows[0] || []).map((h, i) => dateColumns.includes(h) ? i : -1).filter(i => i >= 0));
  const body = rows.map((r, ri) => `<row r="${ri + 1}">${r.map((v, ci) =>
    xlsxCell(v, colName(ci) + (ri + 1), ri > 0 && dates.has(ci))).join("")}</row>`).join("");
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetData>${body}</sheetData></worksheet>`;
}

const PARTS = sheetName => ({
  "[Content_Types].xml": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>`,
  "_rels/.rels": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`,
  "xl/workbook.xml": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="${xml(sheetName)}" sheetId="1" r:id="rId1"/></sheets></workbook>`,
  "xl/_rels/workbook.xml.rels": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`,
  "xl/styles.xml": `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`,
});

// --- a minimal zip writer (deflate where the runtime has CompressionStream, else stored) ---

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = bytes => {
  let c = 0xFFFFFFFF;
  for (const b of bytes) c = CRC_TABLE[(c ^ b) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
};

async function deflate(bytes) {
  if (typeof CompressionStream === "undefined") return null;
  const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("deflate-raw"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function zip(files) {
  const enc = new TextEncoder();
  const chunks = [], central = [];
  let offset = 0;
  for (const [name, text] of Object.entries(files)) {
    const nameBytes = enc.encode(name), data = enc.encode(text);
    const packed = await deflate(data);
    const method = packed ? 8 : 0, body = packed || data, crc = crc32(data);
    const header = (sig, extra) => {
      const h = new DataView(new ArrayBuffer(extra ? 46 : 30));
      h.setUint32(0, sig, true);
      let p = 4;
      if (extra) h.setUint16(p, 20, true), p += 2;            // version made by
      h.setUint16(p, 20, true); h.setUint16(p + 2, 0x0800, true); // version needed, UTF-8 names
      h.setUint16(p + 4, method, true); h.setUint32(p + 6, 33 << 16, true); // method, DOS time 00:00 date 1980-01-01
      h.setUint32(p + 10, crc, true); h.setUint32(p + 14, body.length, true); h.setUint32(p + 18, data.length, true);
      h.setUint16(p + 22, nameBytes.length, true);
      if (extra) h.setUint32(42, offset, true);
      return new Uint8Array(h.buffer);
    };
    const local = header(0x04034B50, false);
    chunks.push(local, nameBytes, body);
    central.push(header(0x02014B50, true), nameBytes);
    offset += local.length + nameBytes.length + body.length;
  }
  const centralSize = central.reduce((n, c) => n + c.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054B50, true);
  end.setUint16(8, Object.keys(files).length, true); end.setUint16(10, Object.keys(files).length, true);
  end.setUint32(12, centralSize, true); end.setUint32(16, offset, true);
  return new Uint8Array(await new Blob([...chunks, ...central, new Uint8Array(end.buffer)]).arrayBuffer());
}

export const toXlsx = (rows, { dateColumns = [], sheetName = "records" } = {}) =>
  zip({ ...PARTS(sheetName), "xl/worksheets/sheet1.xml": sheetXml(rows, dateColumns) });

export const CSV_TYPE = "text/csv;charset=utf-8";
export const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
