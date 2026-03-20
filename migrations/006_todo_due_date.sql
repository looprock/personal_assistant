-- Add due_date to todos; when past due the UI flags the item as urgent
ALTER TABLE todos ADD COLUMN IF NOT EXISTS due_date TIMESTAMPTZ;
