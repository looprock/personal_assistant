-- Add user-editable notes field to todos

ALTER TABLE todos ADD COLUMN IF NOT EXISTS notes TEXT;
