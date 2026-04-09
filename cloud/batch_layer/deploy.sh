#!/bin/bash
# Batch Layer Deployment Script
# Usage: ./deploy.sh [environment] [component]

set -e

# Default values
ENVIRONMENT=${1:-dev}
COMPONENT=${2:-all}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to deploy entire batch layer
deploy_all() {
    echo "🏗️ Deploying entire batch layer..."
    
    # First, deploy infrastructure (creates ECR repository)
    echo "🏗️ Deploying infrastructure..."
    cd infrastructure
    terraform init
    terraform plan -var="environment=$ENVIRONMENT"
    terraform apply -var="environment=$ENVIRONMENT" -auto-approve
    cd ..
    
    # Then, build and push container (now that ECR exists)
    echo "🐳 Building and pushing container..."
    if [ -f "infrastructure/processing/build_batch_container.sh" ]; then
        cd infrastructure/processing
        ./build_batch_container.sh
        cd ../..
    fi
    
    # Deploy AWS Batch job definitions
    echo "📦 Deploying AWS Batch job definitions..."
    if [ -f "infrastructure/processing/deploy_batch_jobs.sh" ]; then
        cd infrastructure/processing
        ./deploy_batch_jobs.sh
        cd ../..
    fi
    
    # Deploy Step Functions orchestration
    echo "🔄 Deploying Step Functions..."
    if [ -f "infrastructure/orchestration/deploy_step_functions.sh" ]; then
        cd infrastructure/orchestration
        ./deploy_step_functions.sh
        cd ../..
    fi
}

# Function to build deployment artifacts first
build_artifacts() {
    local skip_ecr_push=${1:-false}
    
    echo "📦 Building deployment artifacts..."
    
    # Build Lambda packages
    FETCH_DEPLOY="infrastructure/fetching/deploy_lambda.sh"
    if [ ! -f "$FETCH_DEPLOY" ] && [ -f "infrastructure/fetching/deployment_packages/deploy_lambda.sh" ]; then
        FETCH_DEPLOY="infrastructure/fetching/deployment_packages/deploy_lambda.sh"
    fi
    if [ -f "$FETCH_DEPLOY" ]; then
        echo "🔧 Building and deploying Lambda packages (fetching)..."
        cd "$(dirname "$FETCH_DEPLOY")"
        ./deploy_lambda.sh
        cd ../..
    fi
    if [ -f "infrastructure/ingesting/deploy_lambda.sh" ]; then
        echo "🔧 Building and deploying Lambda packages (ingesting)..."
        cd infrastructure/ingesting
        ./deploy_lambda.sh
        cd ../..
    fi
    
    # Build container images (but only push to ECR if not skipping)
    if [ -f "infrastructure/processing/build_batch_container.sh" ]; then
        echo "🐳 Building container images..."
        cd infrastructure/processing
        if [ "$skip_ecr_push" = "true" ]; then
            # Build only, don't push to ECR
            echo "🔧 Building container (skipping ECR push)..."
            ./build_batch_container.sh --local-only
        else
            # Build and push to ECR
            ./build_batch_container.sh
        fi
        cd ../..
    fi
}

# Function to deploy specific component
deploy_component() {
    local component=$1
    echo "🚀 Deploying $component component..."
    
    cd infrastructure
    terraform init
    terraform plan -var="environment=$ENVIRONMENT" -target="module.$component"
    terraform apply -var="environment=$ENVIRONMENT" -target="module.$component" -auto-approve
    cd ..
}

# Function to validate prerequisites
validate_prerequisites() {
    echo "🔍 Validating prerequisites..."
    
    # Check Terraform
    if ! command -v terraform &> /dev/null; then
        echo "❌ Terraform not found. Please install Terraform."
        exit 1
    fi
    
    # Check Docker (for container builds)
    if [ "$COMPONENT" = "processing" ] || [ "$COMPONENT" = "all" ]; then
        if ! command -v docker &> /dev/null; then
            echo "❌ Docker not found. Please install Docker."
            exit 1
        fi
    fi
    
    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        echo "❌ AWS credentials not configured. Please run 'aws configure'."
        exit 1
    fi
    
    echo "✅ Prerequisites validated!"
}

# Function to show deployment summary
show_summary() {
    echo ""
    echo "🎉 Deployment Summary:"
    echo "===================="
    echo "Environment: $ENVIRONMENT"
    echo "Component: $COMPONENT"
    echo "Timestamp: $(date)"
    
    if [ "$COMPONENT" = "all" ]; then
        echo ""
        echo "📋 Deployed Components:"
        echo "  ✅ Lambda Fetchers (OHLCV + Meta + OHLCV planner)"
        echo "  ✅ AWS Batch Jobs (Consolidator + Resampler)"
        echo "  ✅ Step Functions Pipeline"
        echo "  ✅ EventBridge Schedule (21:00 UTC Mon-Fri)"
        echo ""
        echo "🔗 AWS Console Links:"
        echo "  • Lambda: https://ca-west-1.console.aws.amazon.com/lambda"
        echo "  • Batch: https://ca-west-1.console.aws.amazon.com/batch"
        echo "  • Step Functions: https://ca-west-1.console.aws.amazon.com/states"
    else
        echo ""
        echo "📋 Deployed Component: $COMPONENT"
        echo ""
        echo "🔗 Next Steps:"
        echo "  1. Check component in AWS Console"
        echo "  2. Test functionality"
        echo "  3. Monitor CloudWatch logs"
    fi
}

# Main execution
echo "🚀 Deploying Batch Layer - Environment: $ENVIRONMENT, Component: $COMPONENT"

case $COMPONENT in
    "all")
        validate_prerequisites
        build_artifacts true  # Skip ECR push during build
        deploy_all
        show_summary
        ;;
    "ingesting")
        validate_prerequisites
        if [ -f "infrastructure/ingesting/deploy_lambda.sh" ]; then
            cd infrastructure/ingesting
            ./deploy_lambda.sh
            cd ../..
        fi
        show_summary
        ;;
    "fetching"|"processing"|"database")
        validate_prerequisites
        if [ "$COMPONENT" = "fetching" ]; then
            FETCH_DEPLOY="infrastructure/fetching/deploy_lambda.sh"
            if [ ! -f "$FETCH_DEPLOY" ] && [ -f "infrastructure/fetching/deployment_packages/deploy_lambda.sh" ]; then
                FETCH_DEPLOY="infrastructure/fetching/deployment_packages/deploy_lambda.sh"
            fi
            if [ -f "$FETCH_DEPLOY" ]; then
                cd "$(dirname "$FETCH_DEPLOY")"
                ./deploy_lambda.sh
                cd ../..
            fi
        elif [ "$COMPONENT" = "processing" ]; then
            # Build container for processing
            if [ -f "infrastructure/processing/build_batch_container.sh" ]; then
                cd infrastructure/processing
                ./build_batch_container.sh
                cd ../..
            fi
            # Deploy Batch job definitions
            if [ -f "infrastructure/processing/deploy_batch_jobs.sh" ]; then
                cd infrastructure/processing
                ./deploy_batch_jobs.sh
                cd ../..
            fi
        fi
        deploy_component $COMPONENT
        show_summary
        ;;
    "build")
        validate_prerequisites
        build_artifacts
        ;;
    "orchestration")
        echo "🔄 Deploying Step Functions orchestration..."
        validate_prerequisites
        if [ -f "infrastructure/orchestration/deploy_step_functions.sh" ]; then
            cd infrastructure/orchestration
            ./deploy_step_functions.sh
            cd ../..
        fi
        ;;
    "init")
        echo "🔧 Initializing infrastructure..."
        cd infrastructure
        terraform init
        cd ..
        ;;
    "plan")
        echo "📋 Planning infrastructure..."
        cd infrastructure
        terraform plan -var="environment=$ENVIRONMENT"
        cd ..
        ;;
    "destroy")
        echo "💥 Destroying infrastructure..."
        read -p "Are you sure you want to destroy all resources? (yes/no): " confirm
        if [ "$confirm" = "yes" ]; then
            cd infrastructure
            terraform destroy -var="environment=$ENVIRONMENT" -auto-approve
            cd ..
        else
            echo "❌ Destruction cancelled."
        fi
        ;;
    *)
        echo "❌ Invalid component: $COMPONENT"
        echo ""
        echo "Usage: ./deploy.sh [environment] [component]"
        echo ""
        echo "Environments: dev, staging, prod"
        echo "Components:"
        echo "  all           - Deploy entire batch layer (default)"
        echo "  fetching      - Deploy fetcher Lambda functions (Polygon → S3)"
        echo "  ingesting     - Deploy OHLCV + meta ingest Lambdas (VPC → RDS; planner is under fetching)"
        echo "  processing    - Deploy AWS Batch jobs (container + job definitions)"
        echo "  database      - Deploy database infrastructure"
        echo "  orchestration - Deploy Step Functions pipeline"
        echo "  build         - Build artifacts only"
        echo "  init          - Initialize Terraform"
        echo "  plan          - Plan infrastructure"
        echo "  destroy       - Destroy infrastructure"
        echo ""
        echo "Examples:"
        echo "  ./deploy.sh dev all                    # Deploy everything to dev"
        echo "  ./deploy.sh dev fetching               # Deploy only Lambda fetchers"
        echo "  ./deploy.sh dev ingesting              # Deploy OHLCV ingest + planner Lambdas"
        echo "  ./deploy.sh dev processing             # Deploy only Batch jobs"
        echo "  ./deploy.sh dev orchestration          # Deploy only Step Functions"
        exit 1
        ;;
esac

echo "✅ Deployment completed successfully!"