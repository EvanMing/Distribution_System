import time
from pyspark.sql import SparkSession
from pyspark.ml.feature import Tokenizer, StopWordsRemover, HashingTF, IDF, StringIndexer, IndexToString
from pyspark.ml.classification import LogisticRegression
from pyspark.ml.evaluation import MulticlassClassificationEvaluator
from pyspark.ml import Pipeline

def main():
    
    # Spark 读取用：带 s3://
    S3_PATH_PREFIX = "s3://distributed-system-bucket-project"
    # boto3 上传用：不带 s3://
    S3_BUCKET_NAME = "distributed-system-bucket-project"
    
    # 1. Initialize SparkSession
    spark = SparkSession.builder \
        .appName("CrimeDescriptionClassification") \
        .getOrCreate()
    
    # Disable excessive INFO logs to keep the console output clean
    spark.sparkContext.setLogLevel("ERROR")

    print("Loading data...")
    
    # 2. Load the dataset (using sep="\t" and quote="" to bypass dirty data formatting)
    train_df = spark.read.csv(f"{S3_PATH_PREFIX}/train(in).csv", header=True, sep="\t", quote="")
    test_df = spark.read.csv(f"{S3_PATH_PREFIX}/test(in).csv", header=True, sep="\t", quote="")

    # Filter out missing values in target and feature columns
    train_df = train_df.dropna(subset=["Category", "Description"])
    test_df = test_df.dropna(subset=["Category", "Description"])

    # Count dataset sizes
    print("Counting dataset rows...")
    train_count = train_df.count()
    test_count = test_df.count()

    print("Building Machine Learning Pipeline...")
    # 3. Data Preprocessing and Feature Engineering
    label_indexer = StringIndexer(inputCol="Category", outputCol="label", handleInvalid="skip")
    tokenizer = Tokenizer(inputCol="Description", outputCol="words")
    remover = StopWordsRemover(inputCol="words", outputCol="filtered_words")
    hashing_tf = HashingTF(inputCol="filtered_words", outputCol="rawFeatures", numFeatures=10000)
    idf = IDF(inputCol="rawFeatures", outputCol="features")

    # 4. Define the classification algorithm model (Logistic Regression)
    lr = LogisticRegression(featuresCol="features", labelCol="label", maxIter=20, regParam=0.1)

    # Build the Pipeline
    pipeline = Pipeline(stages=[label_indexer, tokenizer, remover, hashing_tf, idf, lr])

    print("Training the model... (This may take a while depending on EMR cluster size)")
    # 5. Train the model and measure training time
    start_train_time = time.time()
    model = pipeline.fit(train_df)
    end_train_time = time.time()
    training_duration = end_train_time - start_train_time

    # 6. Predict and Evaluate
    print("Evaluating model...")
    start_eval_time = time.time()
    
    train_predictions = model.transform(train_df)
    test_predictions = model.transform(test_df)

    # Calculate Accuracy
    evaluator = MulticlassClassificationEvaluator(labelCol="label", predictionCol="prediction", metricName="accuracy")

    train_accuracy = evaluator.evaluate(train_predictions)
    test_accuracy = evaluator.evaluate(test_predictions)
    
    end_eval_time = time.time()
    eval_duration = end_eval_time - start_eval_time

    # 7. Format the output results with additional metrics
    result_text = (
        "========================================\n"
        " Crime Classification Evaluation Results \n"
        "========================================\n"
        f"Training Dataset Size:   {train_count} rows\n"
        f"Test Dataset Size:       {test_count} rows\n"
        "----------------------------------------\n"
        f"Model Training Time:     {training_duration:.2f} seconds\n"
        f"Model Evaluation Time:   {eval_duration:.2f} seconds\n"
        "----------------------------------------\n"
        f"Model Training Accuracy: {train_accuracy:.4f}\n"
        f"Model Test Accuracy:     {test_accuracy:.4f}\n"
        "========================================\n"
    )

    # Print to MobaXterm console
    print(result_text)
    
    # output_filename = f"results/evaluation_results_{int(time.time())}.txt"
    
    # s3_client = boto3.client('s3', region_name='us-east-1')
    # s3_client.put_object(
    # Bucket=S3_BUCKET_NAME, 
    # Key = output_filename, 
    # Body = result_text)
    
    # print(f"Results have been successfully saved to: {output_filename}")

    # (Optional) Show 5 sample predictions from the test set
    print("\nSample Predictions:")
    label_converter = IndexToString(inputCol="prediction", outputCol="predicted_category", labels=model.stages[0].labels)
    test_predictions_with_labels = label_converter.transform(test_predictions)
    test_predictions_with_labels.select("Description", "Category", "predicted_category").show(5, truncate=False)

    # Stop Spark session
    spark.stop()

if __name__ == "__main__":
    main()