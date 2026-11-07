-- Fix: the reports_guard trigger writes the report_edits audit rows, but `authenticated` has no INSERT grant on
-- report_edits, so a manager's report_json UPDATE failed with "permission denied for table report_edits".
-- Run the guard as its owner (auth.uid() still reads the caller's JWT claims), pin the search_path.
alter function public.reports_guard() security definer set search_path = public, pg_temp;
