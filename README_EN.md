<div align="center">
  <img src="assets/icons/irbis64_control_icon.png" alt="IRBIS64 Control icon" width="128">
  <h1>IRBIS64 Control</h1>
  <p>A desktop application for checking IRBIS64 library records against Excel lists.</p>
  <p>
    <a href="https://github.com/VseMirka200/irbis64-control/releases/latest/download/IRBIS64Control-windows-x64.zip"><img src="https://img.shields.io/badge/-DOWNLOAD-555555?style=for-the-badge&amp;logo=github" alt="Download"></a>&nbsp;
    <a href="README.md"><img src="https://img.shields.io/badge/-РУССКИЙ-2468dc?style=for-the-badge" alt="Русский"></a>&nbsp;
    <a href="https://github.com/VseMirka200/irbis64-control/issues/new"><img src="https://img.shields.io/badge/-ISSUE-dc3545?style=for-the-badge&amp;logo=github" alt="Report an issue"></a>
  </p>
</div>

IRBIS64 Control compares IRBIS64 database records with controlled substance lists and the Russian foreign agents registry. Matches can be exported to an Excel report and marked in the database with service fields. The application works directly with an IRBIS64 server or with a local TXT copy of a database.

## Features

- record matching by ISBN, title, author, publisher, and publication year;
- custom matching rules that combine multiple fields;
- author and related-person checks against the foreign agents registry;
- separation of exact and possible matches;
- a dedicated list of results that require manual review;
- Excel reports with selectable sections;
- service markers for IRBIS64 records and local TXT databases;
- change preview before records are written to the server;
- original-record backups for recovery;
- comparison of two Excel reports or two TXT database copies;
- an operation history log;
- automatic checks for new application releases.

## Matching rules

The **Sources → Matching settings** section lets you select the fields that must match. Examples include:

- ISBN;
- title and author;
- title, publisher, and year;
- author, publisher, and year;
- ISBN and year.

Rules are applied independently. A record is considered found when it satisfies at least one enabled rule. Field order does not matter, and duplicate rules are not added.

The application recognizes common Excel column names, including **Автор**, **Название**, **ISBN**, **Издательство**, **Издатель**, **Изд-во**, **Год**, **Год издания**, `Publisher`, and `Year`.

Title matching ignores letter case, redundant punctuation, age ratings, and edition type labels. Ambiguous years and incomplete data are not used to confirm an exact match.

## Working with results

Exact matches with 100% confidence can be used for automatic marker placement. Possible matches are added to a manual-review report and never modify the database.

Before writing to the server, the application reads the matched records again, verifies their MFN versions, and displays the planned changes. A record changed by another user is skipped. A backup of the original records can be created before applying changes.

## Operating modes

**IRBIS64 connection.** The application retrieves records from the server, checks them, and writes confirmed markers after user approval.

**Local TXT database.** The application reads an IRBIS64 export, creates a modified copy, and keeps the source file unchanged.

**Report only.** Results are saved to Excel without adding markers to the database.

**File comparison.** Added, removed, and changed records can be reviewed between two reports or database snapshots.

[Code of Conduct](.github/CODE_OF_CONDUCT.md) · [Contributing](.github/CONTRIBUTING_EN.md) · [Security](.github/SECURITY.md)
