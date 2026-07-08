package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"time"

	"payment-worker/daa"

	_ "github.com/lib/pq"
	amqp "github.com/rabbitmq/amqp091-go"
)

type PaymentJob struct {
	TransactionID string  `json:"transaction_id"`
	UserID        string  `json:"user_id"`
	Amount        float64 `json:"amount"`
	Currency      string  `json:"currency"`
	TraceID       string  `json:"trace_id"`
}

var db *sql.DB
var daaClient *daa.Client

func main() {
	// Init DAA SDK
	daaClient = daa.NewClient(
		os.Getenv("DAA_BACKEND_API_URL"),
		os.Getenv("DAA_TOKEN"),
		"payment-worker",
	)

	// Init PostgreSQL
	var err error
	db, err = sql.Open("postgres", os.Getenv("DATABASE_URL"))
	if err != nil {
		log.Fatalf("Failed to connect to DB: %v", err)
	}
	defer db.Close()

	// Connect to RabbitMQ with retry
	var conn *amqp.Connection
	for i := 0; i < 30; i++ {
		conn, err = amqp.Dial(fmt.Sprintf("amqp://guest:guest@%s:5672/", os.Getenv("RABBITMQ_HOST")))
		if err == nil {
			break
		}
		log.Printf("Waiting for RabbitMQ... (%d/30)", i+1)
		time.Sleep(2 * time.Second)
	}
	if err != nil {
		log.Fatalf("Failed to connect to RabbitMQ: %v", err)
	}
	defer conn.Close()

	ch, err := conn.Channel()
	if err != nil {
		log.Fatalf("Failed to open channel: %v", err)
	}
	defer ch.Close()

	q, err := ch.QueueDeclare("payment_jobs", true, false, false, false, nil)
	if err != nil {
		log.Fatalf("Failed to declare queue: %v", err)
	}

	msgs, err := ch.Consume(q.Name, "", false, false, false, false, nil)
	if err != nil {
		log.Fatalf("Failed to register consumer: %v", err)
	}

	log.Println("Payment worker started. Waiting for jobs...")

	for msg := range msgs {
		var job PaymentJob
		if err := json.Unmarshal(msg.Body, &job); err != nil {
			daaClient.CaptureException(fmt.Errorf("JSON unmarshal error: %w", err))
			msg.Nack(false, false)
			continue
		}

		if err := processPayment(job); err != nil {
			daaClient.CaptureException(err)
			msg.Nack(false, true) // requeue
		} else {
			msg.Ack(false)
		}
	}
}

func processPayment(job PaymentJob) error {
	log.Printf("[%s] Processing payment: $%.2f for user %s", job.TraceID, job.Amount, job.UserID)

	if job.TransactionID == "" {
		return fmt.Errorf("missing transaction_id in payment job")
	}

	// Simulate payment gateway processing
	time.Sleep(100 * time.Millisecond)

	// Reject payments over $10,000
	if job.Amount > 10000 {
		updateStatus(job.TransactionID, "DECLINED")
		return fmt.Errorf("payment declined: amount $%.2f exceeds limit", job.Amount)
	}

	// Update transaction status in PostgreSQL
	return updateStatus(job.TransactionID, "COMPLETED")
}

func updateStatus(txnID, status string) error {
	_, err := db.Exec(
		"UPDATE transactions SET status = $1, updated_at = NOW() WHERE transaction_id = $2",
		status, txnID,
	)
	if err != nil {
		return fmt.Errorf("DB update failed for %s: %w", txnID, err)
	}
	log.Printf("Transaction %s → %s", txnID, status)
	return nil
}
