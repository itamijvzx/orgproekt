
CREATE TABLE change_proposals (
	owner_id VARCHAR(36) NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	current_version INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES user_accounts (id)
)

;
-- statement-break
CREATE INDEX ix_change_proposals_owner_id ON change_proposals (owner_id);
-- statement-break

CREATE TABLE change_receipts (
	proposal_id VARCHAR(36) NOT NULL, 
	actor_id VARCHAR(36) NOT NULL, 
	idempotency_key VARCHAR(80) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(proposal_id) REFERENCES change_proposals (id), 
	FOREIGN KEY(actor_id) REFERENCES user_accounts (id), 
	UNIQUE (idempotency_key)
)

;
-- statement-break

CREATE TABLE change_versions (
	proposal_id VARCHAR(36) NOT NULL, 
	number INTEGER NOT NULL, 
	title VARCHAR(180) NOT NULL, 
	effective_date DATE NOT NULL, 
	change_reason TEXT NOT NULL, 
	created_by VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT change_version_once UNIQUE (proposal_id, number), 
	FOREIGN KEY(proposal_id) REFERENCES change_proposals (id), 
	FOREIGN KEY(created_by) REFERENCES user_accounts (id)
)

;
-- statement-break
CREATE INDEX ix_change_versions_proposal_id ON change_versions (proposal_id);
-- statement-break

CREATE TABLE change_entries (
	proposal_id VARCHAR(36) NOT NULL, 
	version_id VARCHAR(36) NOT NULL, 
	department_id VARCHAR(36), 
	actor_id VARCHAR(36) NOT NULL, 
	recipient_id VARCHAR(36), 
	kind VARCHAR(32) NOT NULL, 
	text TEXT NOT NULL, 
	outcome VARCHAR(32), 
	payload JSONB NOT NULL, 
	due_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(proposal_id) REFERENCES change_proposals (id), 
	FOREIGN KEY(version_id) REFERENCES change_versions (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id), 
	FOREIGN KEY(actor_id) REFERENCES user_accounts (id), 
	FOREIGN KEY(recipient_id) REFERENCES user_accounts (id)
)

;
-- statement-break
CREATE INDEX ix_change_entries_department_id ON change_entries (department_id);
-- statement-break
CREATE INDEX ix_change_entries_proposal_id ON change_entries (proposal_id);
-- statement-break
CREATE INDEX ix_change_entries_version_id ON change_entries (version_id);
-- statement-break

CREATE TABLE change_sections (
	version_id VARCHAR(36) NOT NULL, 
	department_id VARCHAR(36) NOT NULL, 
	title VARCHAR(180) NOT NULL, 
	rationale TEXT NOT NULL, 
	items JSONB NOT NULL, 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT change_department_once UNIQUE (version_id, department_id), 
	FOREIGN KEY(version_id) REFERENCES change_versions (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id)
)

;
-- statement-break
CREATE INDEX ix_change_sections_department_id ON change_sections (department_id);
-- statement-break
CREATE INDEX ix_change_sections_version_id ON change_sections (version_id);
-- statement-break

CREATE TABLE change_outbox (
	entry_id VARCHAR(36) NOT NULL, 
	proposal_id VARCHAR(36) NOT NULL, 
	department_id VARCHAR(36) NOT NULL, 
	recipient_id VARCHAR(36) NOT NULL, 
	kind VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	delivered_at TIMESTAMP WITH TIME ZONE, 
	read_at TIMESTAMP WITH TIME ZONE, 
	processed_at TIMESTAMP WITH TIME ZONE, 
	attempts INTEGER NOT NULL, 
	error_code VARCHAR(64), 
	id VARCHAR(36) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT change_notice_once UNIQUE (entry_id, recipient_id), 
	FOREIGN KEY(entry_id) REFERENCES change_entries (id), 
	FOREIGN KEY(proposal_id) REFERENCES change_proposals (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id), 
	FOREIGN KEY(recipient_id) REFERENCES user_accounts (id)
)

;
-- statement-break
CREATE INDEX ix_change_outbox_proposal_id ON change_outbox (proposal_id);
-- statement-break
CREATE INDEX ix_change_outbox_recipient_id ON change_outbox (recipient_id);