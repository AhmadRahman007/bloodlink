// BloodLink: Part II build pipeline
// Plugins used: Git (git step), Pipeline (declarative syntax), Docker Pipeline (docker.image().inside),
// GitHub (push trigger) and Email Extension (emailext).
pipeline {
    agent any

    triggers {
        githubPush()   // run on every push received through the GitHub webhook
    }

    options {
        timeout(time: 10, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    environment {
        REPO_URL       = 'https://github.com/AhmadRahman007/bloodlink.git'
        COMPOSE_FILE   = 'docker-compose.jenkins.yml'
        HEALTH_URL     = 'http://localhost:8001/health'
    }

    stages {
        stage('Checkout') {
            steps {
                git branch: 'main', url: "${REPO_URL}"
                script {
                    env.COMMITTER_NAME  = sh(script: "git log -1 --pretty=format:'%an'", returnStdout: true).trim()
                    env.COMMITTER_EMAIL = sh(script: "git log -1 --pretty=format:'%ae'", returnStdout: true).trim()
                    env.COMMIT_MSG      = sh(script: "git log -1 --pretty=format:'%s'",  returnStdout: true).trim()
                    env.COMMIT_SHA      = sh(script: "git rev-parse --short HEAD",       returnStdout: true).trim()
                }
                echo "Commit ${env.COMMIT_SHA} by ${env.COMMITTER_NAME} <${env.COMMITTER_EMAIL}>: ${env.COMMIT_MSG}"
            }
        }

        stage('Validate code in container') {
            steps {
                script {
                    // Docker Pipeline plugin: run checks inside a throwaway python container
                    docker.image('python:3.12-slim').inside {
                        sh 'python --version'
                        sh 'python -c "import ast, sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]; print(\'Syntax OK:\', *sys.argv[1:])" app/*.py'
                    }
                }
                sh 'docker compose -f ${COMPOSE_FILE} config --quiet && echo "Compose file is valid"'
            }
        }

        stage('Build & deploy containers') {
            steps {
                sh 'docker compose -f ${COMPOSE_FILE} pull db'
                sh 'docker compose -f ${COMPOSE_FILE} up -d --force-recreate --remove-orphans'
                sh 'docker compose -f ${COMPOSE_FILE} ps'
            }
        }

        stage('Health check') {
            steps {
                sh '''
                    for i in $(seq 1 30); do
                        if curl -fsS "$HEALTH_URL"; then
                            echo ""
                            echo "BloodLink is up on port 8001"
                            exit 0
                        fi
                        echo "Waiting for app... ($i/30)"
                        sleep 5
                    done
                    echo "App did not become healthy in time"
                    docker compose -f "$COMPOSE_FILE" logs --tail=60
                    exit 1
                '''
            }
        }
    }

    post {
        always {
            script {
                // email whoever authored the pushed commit
                def to = env.COMMITTER_EMAIL ?: ''
                if (!to) {
                    echo 'No committer email found, skipping notification email.'
                    return
                }
                if (to.endsWith('noreply.github.com')) {
                    echo "Warning: committer uses a GitHub noreply address (${to}); the email may not be delivered."
                }
                def result = currentBuild.currentResult
                def color = result == 'SUCCESS' ? '#1f7a4d' : '#b3122e'
                emailext(
                    to: to,
                    subject: "BloodLink build #${env.BUILD_NUMBER}: ${result}",
                    mimeType: 'text/html',
                    attachLog: true,
                    body: """
                        <h2 style="color:${color}">Build #${env.BUILD_NUMBER} ${result}</h2>
                        <p><b>Commit:</b> ${env.COMMIT_SHA ?: 'n/a'} &ndash; ${env.COMMIT_MSG ?: 'n/a'}</p>
                        <p><b>Pushed by:</b> ${env.COMMITTER_NAME ?: 'n/a'} (${env.COMMITTER_EMAIL ?: 'n/a'})</p>
                        <p><b>Duration:</b> ${currentBuild.durationString}</p>
                        <p><b>App:</b> port 8001 on the Jenkins server (<code>/health</code> for status)</p>
                        <p><a href="${env.BUILD_URL}console">Open the console output</a> (full log attached)</p>
                    """
                )
            }
        }
    }
}