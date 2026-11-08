
CREATE TABLE reports (
	proposal_id VARCHAR(36) NOT NULL, 
	owner_id VARCHAR(36) NOT NULL, 
	reviewer_id VARCHAR(36) NOT NULL, 
	previous_id VARCHAR(36), 
	kind VARCHAR(20) NOT NULL, 
	departments JSONB NOT NULL, 
	recipients JSONB NOT NULL, 
	state VARCHAR(20) NOT NULL, 
	version INTEGER NOT NULL, 
	revision INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(proposal_id) REFERENCES change_proposals (id), 
	FOREIGN KEY(owner_id) REFERENCES user_accounts (id), 
	FOREIGN KEY(reviewer_id) REFERENCES user_accounts (id), 
	UNIQUE (previous_id), 
	FOREIGN KEY(previous_id) REFERENCES reports (id)
)

;
-- statement-break

CREATE TABLE report_events (
	report_id VARCHAR(36) NOT NULL, 
	revision INTEGER NOT NULL, 
	actor_id VARCHAR(36) NOT NULL, 
	kind VARCHAR(24) NOT NULL, 
	note TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(report_id) REFERENCES reports (id), 
	FOREIGN KEY(actor_id) REFERENCES user_accounts (id)
)

;
-- statement-break

CREATE TABLE report_receipts (
	report_id VARCHAR(36) NOT NULL, 
	actor_id VARCHAR(36) NOT NULL, 
	idempotency_key VARCHAR(80) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(report_id) REFERENCES reports (id), 
	FOREIGN KEY(actor_id) REFERENCES user_accounts (id), 
	UNIQUE (idempotency_key)
)

;
-- statement-break

CREATE TABLE report_releases (
	report_id VARCHAR(36) NOT NULL, 
	revision INTEGER NOT NULL, 
	issuer_id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	manifest JSONB NOT NULL, 
	pdf BYTEA NOT NULL, 
	docx BYTEA NOT NULL, 
	pdf_sha256 VARCHAR(64) NOT NULL, 
	docx_sha256 VARCHAR(64) NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (report_id), 
	FOREIGN KEY(report_id) REFERENCES reports (id), 
	FOREIGN KEY(issuer_id) REFERENCES user_accounts (id)
)

;
-- statement-break

CREATE TABLE report_revisions (
	report_id VARCHAR(36) NOT NULL, 
	number INTEGER NOT NULL, 
	content JSONB NOT NULL, 
	snapshot JSONB NOT NULL, 
	digest VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (report_id, number), 
	FOREIGN KEY(report_id) REFERENCES reports (id)
)

;
-- statement-break
CREATE INDEX ix_reports_proposal_id ON reports (proposal_id);
-- statement-break
CREATE INDEX ix_report_events_report_id ON report_events (report_id);
-- statement-break
CREATE INDEX ix_report_revisions_report_id ON report_revisions (report_id);
-- statement-break
CREATE FUNCTION protect_released_report() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.state = 'released' THEN
        RAISE EXCEPTION 'A released report is immutable';
    END IF;
    RETURN NEW;
END;
$$;
-- statement-break
CREATE TRIGGER protect_release BEFORE UPDATE ON reports FOR EACH ROW EXECUTE FUNCTION protect_released_report();
