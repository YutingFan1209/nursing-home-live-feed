-- Street address of each home, from CMS's Provider Information file: UCC
-- debtors are often named by it ("9101 SECOND AVENUE OPERATIONS LLC")
ALTER TABLE cms_facilities ADD COLUMN IF NOT EXISTS provider_address TEXT;
